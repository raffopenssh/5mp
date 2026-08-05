"""Download high-res basemap tiles (Esri World Imagery) for an AOI -> single PNG.
Useful for visual validation of mining site detections (Sentinel-2 10m is too
coarse to see artisanal pits clearly)."""
import argparse,io,math,os,sys,urllib.request
from PIL import Image, ImageDraw

SRC={
 "esri":"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
 "esri_clarity":"https://clarity.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}
def deg2num(lon,lat,z):
    n=2**z
    x=(lon+180)/360*n
    y=(1-math.log(math.tan(math.radians(lat))+1/math.cos(math.radians(lat)))/math.pi)/2*n
    return x,y
def num2deg(x,y,z):
    n=2**z
    lon=x/n*360-180
    lat=math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))
    return lon,lat

def fetch(bbox,z,src="esri"):
    x0,y0=deg2num(bbox[0],bbox[3],z); x1,y1=deg2num(bbox[2],bbox[1],z)
    xi0,yi0,xi1,yi1=int(x0),int(y0),int(x1),int(y1)
    W=(xi1-xi0+1)*256; H=(yi1-yi0+1)*256
    out=Image.new("RGB",(W,H))
    for xi in range(xi0,xi1+1):
        for yi in range(yi0,yi1+1):
            url=SRC[src].format(z=z,x=xi,y=yi)
            try:
                req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
                im=Image.open(io.BytesIO(urllib.request.urlopen(req,timeout=30).read())).convert("RGB")
            except Exception as e:
                print("  tile err",xi,yi,str(e)[:50],file=sys.stderr); continue
            out.paste(im,((xi-xi0)*256,(yi-yi0)*256))
    tl=num2deg(xi0,yi0,z); br=num2deg(xi1+1,yi1+1,z)
    return out,(tl[0],br[1],br[0],tl[1])

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--bbox",required=True); ap.add_argument("--z",type=int,default=16)
    ap.add_argument("--out",required=True); ap.add_argument("--src",default="esri")
    ap.add_argument("--marks",default="",help="lon,lat;lon,lat")
    a=ap.parse_args()
    bbox=tuple(float(v) for v in a.bbox.split(","))
    im,ext=fetch(bbox,a.z,a.src)
    if a.marks:
        d=ImageDraw.Draw(im)
        for m in a.marks.split(";"):
            if not m: continue
            lo,la=(float(v) for v in m.split(","))
            px=(lo-ext[0])/(ext[2]-ext[0])*im.width
            py=(ext[3]-la)/(ext[3]-ext[1])*im.height
            d.ellipse([px-9,py-9,px+9,py+9],outline=(255,0,0),width=3)
    os.makedirs(os.path.dirname(a.out) or ".",exist_ok=True)
    im.save(a.out)
    print(a.out,im.size,"extent",ext)
main()
