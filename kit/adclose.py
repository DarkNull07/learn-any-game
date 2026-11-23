"""Close controls Android names, and X glyphs inside small clickable ad controls. Coordinates are in the 1080x2400
reference frame. These are proposals, not proof a tap is safe: only use on a confirmed ad surface, with a fresh Android
dump, and after the spot has stayed put (kit/ads.py, after the known templates)."""
import re
import xml.etree.ElementTree as ET
import cv2
import numpy as np

AD_HINT = re.compile(r'adview|ad[_./-]?webview|moloco|mbridge|applovin|ironsource|vungle|admob|pangle|inmobi|chartboost|fyber|playable', re.I)
DENY = re.compile(r'install|download|\bget\b|\bopen\b|buy|shop|visit|learn|play|sign|watch|coin|\u20b9|interested|store|reward', re.I)
NAMED = re.compile(r'^(x|×|✕|close|close ad|close button|skip|skip ad|skip video|skip this ad|interstitial close button|dismiss)$', re.I)
BOUNDS = re.compile(r'^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$')
SDK = r'(mbridge|applovin|ironsource|vungle|unity|admob|audience|liftoff|moloco|bigo|pangle|inmobi|chartboost|fyber)'
SDK_CLOSE_ID = re.compile(SDK + r'.*(close|skip)|(close|skip).*' + SDK, re.I)  # an ad SDK's own close button id
ADMOB_CLOSE = re.compile(r'^interstitial close button$', re.I)

def ui_candidates(xml, scale=(1, 1), width=1080, height=2400):
    """Return (ad_surface, named controls, unlabeled controls). Never click a whole WebView."""
    try:
        xml = xml[xml.find('<?xml'):] if '<?xml' in xml else xml[xml.find('<hierarchy'):]
        xml = xml.split('</hierarchy>')[0] + '</hierarchy>'
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError):
        return False, [], []
    nodes = list(root.iter('node'))
    ad_surface = any(AD_HINT.search(' '.join((n.get('resource-id',''), n.get('class',''), n.get('content-desc','')))) for n in nodes)
    named, anonymous = [], []
    for n in nodes:
        if n.get('clickable') != 'true' or n.get('enabled', 'true') != 'true':
            continue
        m = BOUNDS.match(n.get('bounds',''))
        if not m:
            continue
        x1,y1,x2,y2 = map(int,m.groups())
        x1,x2 = x1/scale[0],x2/scale[0]
        y1,y2 = y1/scale[1],y2/scale[1]
        w,h = x2-x1,y2-y1
        x,y = (x1+x2)/2,(y1+y2)/2
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height and y < height*.30 and (x < width*.30 or x > width*.70)):
            continue
        if not (20 <= w <= 220 and 20 <= h <= 160):
            continue
        desc,text,rid = n.get('content-desc','').strip(),n.get('text','').strip(),n.get('resource-id','').strip()
        descendants = ' '.join(' '.join((child.get('text',''), child.get('content-desc',''), child.get('resource-id',''))) for child in n.iter('node') if child is not n)
        if DENY.search(' '.join((desc,text,rid,descendants))):
            continue
        if ADMOB_CLOSE.match(desc) or ADMOB_CLOSE.match(text) or SDK_CLOSE_ID.search(rid):
            ad_surface = True
        if NAMED.fullmatch(desc) or NAMED.fullmatch(text) or SDK_CLOSE_ID.search(rid):
            named.append((round(x),round(y)))
        elif ad_surface and not desc and not text and not rid and not descendants.strip() and .5 <= w/max(h,1) <= 2:
            anonymous.append((round(x),round(y),round(w),round(h)))
    return ad_surface,named,anonymous

def _glyph_score(mask):
    """Similarity to a synthetic X, with small tolerance for anti-aliasing."""
    h,w = mask.shape
    if min(h,w) < 10 or not .65 <= w/h <= 1.5:
        return 0.0
    sample = cv2.resize(mask, (32,32), interpolation=cv2.INTER_NEAREST)>0
    best=0.0
    kernel = np.ones((3,3),np.uint8)
    for t in (2,3,4,5,6):
        ref = np.zeros((32,32),np.uint8)
        cv2.line(ref,(3,3),(28,28),1,t); cv2.line(ref,(28,3),(3,28),1,t)
        ref = ref>0
        a=cv2.dilate(sample.astype('uint8'),kernel)>0
        b=cv2.dilate(ref.astype('uint8'),kernel)>0
        p=(sample & b).sum()/max(1,sample.sum()); r=(ref & a).sum()/max(1,ref.sum())
        best=max(best,2*p*r/max(p+r,1e-9))
    return float(best)

def visual_candidates(img):
    """Find isolated X glyphs at the screen's upper corners (does not identify all styles)."""
    H,W=img.shape[:2]
    gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    out=[]
    k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(51,51))
    for x0,x1 in ((0,int(W*.30)), (int(W*.70),W)):
        roi=gray[40:int(H*.30),x0:x1]
        for mode in (cv2.MORPH_TOPHAT,cv2.MORPH_BLACKHAT):
            contrast=cv2.morphologyEx(roi,mode,k)
            mask=(contrast>50).astype('uint8')
            n,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
            for i in range(1,n):
                x,y,w,h,area=map(int,stats[i])
                if not (12<=w<=65 and 12<=h<=65 and 0.10 <= area/(w*h) <= 0.70):
                    continue
                score=_glyph_score((labels[y:y+h,x:x+w]==i).astype('uint8'))
                if score>=.82:
                    out.append((x0+x+w//2,40+y+h//2,score))
    return sorted(out,key=lambda v:v[2],reverse=True)

def corroborated(visual, nodes):
    """Require the glyph center to lie within an independently obtained small clickable node."""
    result=[]
    for x,y,score in visual:
        for nx,ny,w,h in nodes:
            if abs(x-nx)<=w/2 and abs(y-ny)<=h/2:
                result.append((x,y,score))
                break
    return result
