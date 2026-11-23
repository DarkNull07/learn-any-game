"""Live connection to the phone through the scrcpy server (scrcpy\\scrcpy-server-v*): the screen as a low-latency video
stream decoded in a background thread, and touches over the same connection (a tap costs milliseconds and no new
process, and two fingers just work). Nothing is installed on the phone: the server runs from /data/local/tmp only
while connected. If this cannot start or dies, the caller falls back to adb screenshots and taps."""
import glob
import os
import random
import re
import socket
import struct
import subprocess
import threading
import time

import av

HERE = os.path.dirname(os.path.abspath(__file__))
REMOTE = "/data/local/tmp/gamekit-scrcpy.jar"
FLAG_SESSION = 1 << 63  # a 12-byte packet with the screen size, no payload
FLAG_CONFIG = 1 << 62   # codec setup data, to go in front of the next frame
DOWN, UP, MOVE = 0, 1, 2


class Phone:
    def __init__(self, adb_path="adb", port=27199, bit_rate=24_000_000, max_fps=60):
        self.adb = adb_path
        self.port = port
        self.bit_rate, self.max_fps = bit_rate, max_fps
        self.w, self.h = 0, 0
        self.video = self.control = self.server = None
        self.alive = False
        self.count = 0          # frames decoded so far
        self.frame_time = 0.0   # when the newest frame arrived
        self._frame = None      # newest decoded frame (converted to a picture only when someone asks)
        self._img, self._img_n = None, -1
        self._cv = threading.Condition()
        self._send_lock = threading.Lock()

    # ------------------------------------------------------------ start / stop
    def start(self, timeout=10.0):
        jars = sorted(glob.glob(os.path.join(HERE, "scrcpy", "scrcpy-server-v*")))
        if not jars:
            raise RuntimeError("scrcpy server file not found")
        jar = jars[-1]
        version = re.search(r"-v([\d.]+)$", jar).group(1)
        scid = "%08x" % random.randrange(1 << 31)
        subprocess.run([self.adb, "push", jar, REMOTE], capture_output=True, timeout=30, check=True)
        subprocess.run([self.adb, "forward", f"tcp:{self.port}", f"localabstract:scrcpy_{scid}"],
                       capture_output=True, timeout=10, check=True)
        args = [f"scid={scid}", "log_level=warn", "tunnel_forward=true", "audio=false", "control=true", "cleanup=true",
                "video_codec=h264", f"video_bit_rate={self.bit_rate}", f"max_fps={self.max_fps}", "max_size=0",
                "send_device_meta=false", "send_frame_meta=true", "send_dummy_byte=true"]
        self.server = subprocess.Popen([self.adb, "shell", f"CLASSPATH={REMOTE}", "app_process", "/",
                                        "com.genymobile.scrcpy.Server", version] + args,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while True:  # the server needs a moment before it listens; until then adb accepts and closes at once
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                s.settimeout(2)
                if s.recv(1):  # the dummy byte: the server is really there
                    self.video = s
                    break
                s.close()
            except OSError:
                pass
            if time.time() - t0 > timeout:
                self.stop()
                raise RuntimeError("scrcpy server did not answer")
            time.sleep(0.1)
        self.control = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.control.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.video.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.video.settimeout(None)
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._drain_control, daemon=True).start()
        with self._cv:
            self._cv.wait_for(lambda: self.count > 0 or not self.alive, timeout=timeout)
        if not self.count:
            self.stop()
            raise RuntimeError("no video from the scrcpy server")
        return self

    def stop(self):
        self.alive = False
        for s in (self.video, self.control):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        if self.server:
            self.server.terminate()
        subprocess.run([self.adb, "forward", "--remove", f"tcp:{self.port}"], capture_output=True, timeout=10)

    # ------------------------------------------------------------ video
    def _recv(self, n):
        buf = bytearray()
        while len(buf) < n:
            part = self.video.recv(n - len(buf))
            if not part:
                raise OSError("video stream closed")
            buf += part
        return bytes(buf)

    def _reader(self):
        try:
            self._recv(4)  # codec name: "h264"
            ctx = av.CodecContext.create("h264", "r")
            ctx.thread_count = 1  # threads would hold frames back
            config = b""
            while self.alive:
                pts_flags, size = struct.unpack(">QI", self._recv(12))
                if pts_flags & FLAG_SESSION:  # the screen size (it changes if the phone rotates)
                    self.w, self.h = pts_flags & 0xFFFFFFFF, size
                    continue
                data = self._recv(size)
                if pts_flags & FLAG_CONFIG:
                    config = data
                    continue
                for frame in ctx.decode(av.Packet(config + data)):
                    with self._cv:
                        self._frame = frame
                        self.count += 1
                        self.frame_time = time.time()
                        self._cv.notify_all()
                config = b""
        except Exception:  # noqa: BLE001  any failure ends the stream; the caller falls back to adb
            pass
        finally:
            self.alive = False
            with self._cv:
                self._cv.notify_all()

    def latest(self):
        """The newest picture (BGR). Each frame is converted at most once."""
        with self._cv:
            frame, n = self._frame, self.count
        if n != self._img_n:
            self._img, self._img_n = frame.to_ndarray(format="bgr24"), n
        return self._img

    def next_frame(self, timeout=0.15):
        """Waits for a frame newer than the last one handed out (up to timeout), and returns the newest."""
        with self._cv:
            seen = self._img_n
            self._cv.wait_for(lambda: self.count != seen or not self.alive, timeout=timeout)
        return self.latest()

    def _drain_control(self):
        try:
            while self.alive and self.control.recv(4096):
                pass
        except OSError:
            pass

    # ------------------------------------------------------------ touches
    def _send(self, msg):
        with self._send_lock:
            self.control.sendall(msg)

    def _touch(self, action, pid, x, y, pressure=0xFFFF):
        self._send(struct.pack(">BBqiiHHHii", 2, action, pid, int(x), int(y), self.w, self.h,
                               pressure if action != UP else 0, 0, 0))

    def tap(self, x, y, hold=0.012):
        self._touch(DOWN, 0x41, x, y)
        time.sleep(hold)
        self._touch(UP, 0x41, x, y)

    def drag(self, x0, y0, x1, y1, steps=24, step_time=0.016):
        """One finger from (x0, y0) to (x1, y1): pans the board when it starts on an empty spot."""
        self._touch(DOWN, 0x61, x0, y0)
        time.sleep(0.03)
        for i in range(1, steps + 1):
            t = i / steps
            self._touch(MOVE, 0x61, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            time.sleep(step_time)
        time.sleep(0.05)
        self._touch(UP, 0x61, x1, y1)

    def pinch(self, a0, b0, a1, b1, steps=12, step_time=0.016):
        """Two fingers: a0 -> a1 and b0 -> b1."""
        self._touch(DOWN, 0x51, *a0)
        self._touch(DOWN, 0x52, *b0)
        time.sleep(0.03)
        for i in range(1, steps + 1):
            t = i / steps
            self._touch(MOVE, 0x51, a0[0] + (a1[0] - a0[0]) * t, a0[1] + (a1[1] - a0[1]) * t)
            self._touch(MOVE, 0x52, b0[0] + (b1[0] - b0[0]) * t, b0[1] + (b1[1] - b0[1]) * t)
            time.sleep(step_time)
        time.sleep(0.03)
        self._touch(UP, 0x52, *b1)
        self._touch(UP, 0x51, *a1)

    def back(self):
        for action in (0, 1):
            self._send(struct.pack(">BBiii", 0, action, 4, 0, 0))
