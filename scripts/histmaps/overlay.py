import json,subprocess,sys,tempfile,os
import numpy as np, cv2
tif=sys.argv[1]; out=sys.argv[2]
W_OUT=1600
info=json.loads(subprocess.check_output(['gdalinfo','-json',tif]))
gt=info['geoTransform']; W,H=info['size']
tmp=tempfile.mktemp(suffix='.png')
subprocess.check_call(['gdal_translate','-q','-of','PNG','-b','1','-outsize',str(W_OUT),'0',tif,tmp])
img=cv2.imread(tmp,cv2.IMREAD_GRAYSCALE); os.remove(tmp)
[os.remove(tmp+e) for e in ('.aux.xml',) if os.path.exists(tmp+e)]
img=cv2.cvtColor(img,cv2.COLOR_GRAY2BGR)
sc=img.shape[1]/W
def px(lon,lat):
    x=(lon-gt[0])/gt[1]; y=(lat-gt[3])/gt[5]
    return int(round(x*sc)), int(round(y*sc))
# GADM outlines live beside this script (they are committed), not in whatever
# scratch dir the first run happened to use. The old hardcoded /tmp path
# combined with the `continue` below to make a missing file render a perfectly
# plausible *un-annotated* image -- i.e. the check silently not happening.
ROOT=os.path.dirname(os.path.abspath(__file__))
cols={'gadm_CAF.json':(0,0,255),'gadm_SDN.json':(255,0,0),'gadm_SSD.json':(0,180,0)}
missing=[f for f in cols if not os.path.exists(os.path.join(ROOT,f))]
if missing:
    sys.exit(f"missing GADM reference files in {ROOT}: {', '.join(missing)}")
for f,col in cols.items():
    p=os.path.join(ROOT,f)
    d=json.load(open(p))
    for ft in d['features']:
        g=ft['geometry']; polys=g['coordinates'] if g['type']=='MultiPolygon' else [g['coordinates']]
        for poly in polys:
            for ring in poly:
                pts=np.array([px(a,b) for a,b in ring],np.int32)
                cv2.polylines(img,[pts],True,col,2,cv2.LINE_AA)
cv2.imwrite(out,img); print(out, img.shape)
