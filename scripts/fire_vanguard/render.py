import numpy as np, json, datetime, matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.colors as mc
from matplotlib.collections import LineCollection
from scipy import ndimage
from scipy.spatial import cKDTree
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
SEASON="2024"; A=f"{SEASON}-08-01"
LAT0=7.6; KX=111.32*np.cos(np.radians(LAT0)); KY=110.57; X0,Y0=22.7,4.25
EXT=[22.7,31.3,4.25,11.0]
onset=np.load(f"/tmp/fx/onset_{SEASON}.npy"); reg=np.load(f"/tmp/fx/reg_{SEASON}.npy")   # 0.025 deg grid
ny,nx=reg.shape; res=0.025
fill=np.where(np.isnan(reg),0,reg); w=(~np.isnan(reg)).astype(float)
regs=ndimage.gaussian_filter(fill,10)/np.maximum(ndimage.gaussian_filter(w,10),1e-6)
covfrac=ndimage.uniform_filter((~np.isnan(onset)).astype(float),40)   # burned-cell fraction within ~1 deg
regs[(ndimage.gaussian_filter(w,10)<0.5)|np.isnan(reg)]=np.nan
from matplotlib.path import Path as MPath
rec=np.load("/tmp/fx/recur.npy"); cov=np.load("/tmp/fx/cov.npy"); vsum=np.load("/tmp/fx/van_sum.npy")  # 5 km grid
lines=json.load(open("/tmp/fx/lines_2024.json"))
vgroups=json.load(open("/tmp/fx/van_groups_10.json"))
VLEAD=10
settle=np.load("/tmp/fx/settle_xy.npy"); stree=cKDTree(np.c_[(settle[:,0]-X0)*KX,(settle[:,1]-Y0)*KY])
parks=json.load(open("/tmp/fx/parks.json"))
aoi=json.loads(__import__('sqlite3').connect("file:/home/exedev/5mp/db.sqlite3?mode=ro",uri=True).execute("select geometry from aois where id='XSA_Study_Area'").fetchone()[0])

def reg_at(lon,lat):
    ix=np.clip(((lon-EXT[0])/res).astype(int),0,nx-1); iy=np.clip(((lat-EXT[2])/res).astype(int),0,ny-1); return regs[iy,ix]
def rec_at(lon,lat):
    ix=np.clip(((lon-X0)*KX/5).astype(int),0,rec.shape[1]-1); iy=np.clip(((lat-Y0)*KY/5).astype(int),0,rec.shape[0]-1); return rec[iy,ix],cov[iy,ix],vsum[iy,ix]

# per-line features
LEAD=15
for L in lines:
    c=np.array(L['coords']); sd=D(L['start'])-D(A)
    r=reg_at(c[:,0],c[:,1]); pl=r-(sd+np.array(L['offs']))       # per-point: days ahead of the local season front
    L['plead']=pl; L['lead']=float(np.nanmax(pl)) if np.isfinite(pl).any() else np.nan
    L['van_pts']=int(np.nansum(pl>=LEAD))
    rr,cc,vs=rec_at(c[:,0],c[:,1]); ok=cc>=4
    L['recur']=float(np.nanmean(rr[ok])) if ok.any() else np.nan
    d,_=stree.query(np.c_[(c[:,0]-X0)*KX,(c[:,1]-Y0)*KY]); L['settle_km']=float(np.median(d))
    L['sd']=sd
n_van=sum(1 for L in lines if L['van_pts']>=2); n_lines=len(lines)
van=[L for L in lines if L['van_pts']>=2]
remote=[L for L in van if L['settle_km']>=10]; onroute=[L for L in van if np.isfinite(L['recur']) and L['recur']>=0.4]
print(f"lines {n_lines}, ahead>={LEAD}d: {n_van} ({100*n_van/n_lines:.1f}%), of which >=10 km from settlements: {len(remote)}, on recurring corridor: {len(onroute)}")

# ---------- figure ----------
BG='#0c0d10'
fig=plt.figure(figsize=(26,15),facecolor=BG)
ax=fig.add_axes([0.02,0.03,0.68,0.94],facecolor=BG)
cmap=plt.get_cmap('turbo'); norm=mc.Normalize(D(f"{SEASON}-10-15")-D(A),D(f"{int(SEASON)+1}-02-01")-D(A))
def col(d): return cmap(norm(d))
def add_layers(ax,zoom=False):
    ax.set_facecolor(BG)
    # 1 season front: faint fill + isochrones
    ax.imshow(regs,origin='lower',extent=EXT,cmap='turbo',norm=norm,alpha=0.13,aspect='auto',interpolation='bilinear')
    dates=[f"{SEASON}-10-15",f"{SEASON}-11-01",f"{SEASON}-11-15",f"{SEASON}-12-01",f"{SEASON}-12-15",f"{int(SEASON)+1}-01-01",f"{int(SEASON)+1}-01-15",f"{int(SEASON)+1}-02-01"]
    lv=[D(d)-D(A) for d in dates]
    X=np.linspace(EXT[0],EXT[0]+nx*res,nx); Y=np.linspace(EXT[2],EXT[2]+ny*res,ny)
    cs=ax.contour(X,Y,regs,levels=lv,colors=[col(v) for v in lv],linewidths=1.1 if not zoom else 1.6,alpha=0.85)
    ax.clabel(cs,fmt={v:datetime.date.fromisoformat(d).strftime('%-d %b') for v,d in zip(lv,dates)},fontsize=9 if not zoom else 11,colors='w')
    # 2 all existing lines, faint
    segs=[np.array(L['coords']) for L in lines]
    ax.add_collection(LineCollection(segs,colors='#8a8f99',linewidths=0.35 if not zoom else 0.6,alpha=0.16 if not zoom else 0.28))
    # 3 recurring early-burn ground
    ry,rx=np.where((rec>=0.4)&(cov>=4)); qx=rx*5/KX+X0+2.5/KX; qy=ry*5/KY+Y0+2.5/KY
    ag=aoi['coordinates'] if aoi['type']=='Polygon' else aoi['coordinates'][0]; inside=MPath(np.array(ag[0])).contains_points(np.c_[qx,qy]); qx,qy=qx[inside],qy[inside]
    ax.scatter(qx,qy,s=(14 if not zoom else 60),marker='s',c='#5fe3ff',alpha=0.7,linewidths=0,zorder=3)
    # 4 vanguard lines coloured by start date; remote ones brighter/wider
    for g in vgroups:
        t=np.array([[p[0],p[1],D(p[2])-D(A)] for p in g['trajectory']])
        if len(t)<2: continue
        dd,_=stree.query(np.c_[(t[:,0]-X0)*KX,(t[:,1]-Y0)*KY]); rem=np.median(dd)>=10; g['remote']=bool(rem)
        for i in range(len(t)-1):
            ax.plot(t[i:i+2,0],t[i:i+2,1],color=col(t[i,2]),lw=(1.8 if rem else 1.0)*(1 if not zoom else 1.9),alpha=0.95 if rem else 0.65,zorder=4,solid_capstyle='round')
        ax.plot(t[0,0],t[0,1],'o',ms=(2.5 if not zoom else 5),color=col(t[0,2]),alpha=0.9,zorder=4)
    # parks + AOI
    for p in parks:
        g=p['geom']; polys=g['coordinates'] if g['type']=='Polygon' else [q for mp in g['coordinates'] for q in mp]
        for ring in ([polys[0]] if g['type']=='Polygon' else polys):
            r=np.array(ring); 
            if r[:,0].max()<EXT[0] or r[:,0].min()>EXT[1] or r[:,1].max()<EXT[2] or r[:,1].min()>EXT[3]: continue
            ax.plot(r[:,0],r[:,1],color='#4caf6a',lw=0.9,alpha=0.8,zorder=5)
    ag=aoi['coordinates'] if aoi['type']=='Polygon' else aoi['coordinates'][0]
    r=np.array(ag[0]); ax.plot(r[:,0],r[:,1],color='#6ea8ff',lw=1.2,ls='--',alpha=0.9,zorder=5)
    ax.tick_params(colors='#999',labelsize=9)
    for s in ax.spines.values(): s.set_color('#333')
add_layers(ax); ax.set_xlim(EXT[0]-0.1,EXT[1]+0.1); ax.set_ylim(EXT[2]-0.1,EXT[3]+0.1)
ax.set_title(f"XSA fire season {SEASON}/{int(SEASON)+1}: the season front, and the fire that ran ahead of it",color='w',fontsize=17,loc='left',pad=10)
# zoom inset
zx=[23.2,26.4]; zy=[6.6,9.4]
ax.plot([zx[0],zx[1],zx[1],zx[0],zx[0]],[zy[0],zy[0],zy[1],zy[1],zy[0]],color='w',lw=0.8,alpha=0.7,zorder=6)
axz=fig.add_axes([0.715,0.42,0.275,0.55],facecolor=BG); add_layers(axz,zoom=True); axz.set_xlim(zx); axz.set_ylim(zy); axz.set_title("detail",color='w',fontsize=12,loc='left')
# colorbar
cax=fig.add_axes([0.715,0.36,0.275,0.014]); cb=plt.colorbar(plt.cm.ScalarMappable(norm=norm,cmap=cmap),cax=cax,orientation='horizontal')
tick=[D(d)-D(A) for d in [f"{SEASON}-11-01",f"{SEASON}-12-01",f"{int(SEASON)+1}-01-01",f"{int(SEASON)+1}-02-01"]]; cb.set_ticks(tick); cb.set_ticklabels(['1 Nov','1 Dec','1 Jan','1 Feb']); cb.ax.tick_params(colors='w',labelsize=10); cb.outline.set_edgecolor('#555')
cax.set_title("date: when the season front arrived (contours) / when a vanguard chain burned (lines)",color='#ddd',fontsize=10,loc='left')
# legend text
gy_,gx_=np.gradient(regs); gmag=np.hypot(gx_/(res*KX),gy_/(res*KY)); front_speed=1/np.nanmedian(gmag[np.isfinite(gmag)&(gmag>0)])
n_vg=len(vgroups); n_rem=sum(1 for g in vgroups if g.get('remote')); 
import statistics as st_
med_days=st_.median(g['days'] for g in vgroups); med_km=st_.median(g['distance_km'] for g in vgroups); med_sp=st_.median(g['speed_km_day'] for g in vgroups)
months=__import__('collections').Counter(g['start_date'][:7] for g in vgroups); top=", ".join(f"{datetime.date.fromisoformat(m+'-01').strftime('%b')} {n}" for m,n in sorted(months.items()))
txt=(f"HOW TO READ\n\n"
 f"Contours — the SEASON FRONT: the date by which a fifth of the land within 60 km had\n"
 f"burned. It sweeps the study area from the east (Nov) to the west (Jan) at a median {front_speed:.0f} km/day, and\n"
 f"repeats every year. This is where the burning season *was* on each date.\n\n"
 f"Grey hairlines — all {n_lines:,} fire lines of this season, as the app draws them today.\n"
 f"They trace corridors (real), but inside the burning season the day order along a line is\n"
 f"not recoverable: the tracker gives the same lines on day-shuffled data (skill ~0, 6 tests).\n\n"
 f"Coloured chains — {n_vg:,} VANGUARD CHAINS: the same tracker run only on detections that\n"
 f"burned >= {VLEAD} days AHEAD of the front where they lie (1.3% of all detections). There the\n"
 f"field is sparse and the day order IS recoverable: links 2x the shuffled null (skill 0.46),\n"
 f"chains 29 km vs 19 km, long fronts 8 vs 4. Median chain: {med_days:.0f} days, {med_km:.0f} km, {med_sp:.1f} km/day;\n"
 f"by start month: {top}. Bright/wide = >= 10 km from any settlement ({n_rem:,}) — fire\n"
 f"laid in empty country before the season: the scouts' signature. Thin = near villages.\n\n"
 f"Cyan squares — TRADITIONAL EARLY-BURN GROUND: 5 km cells burning >= 15 d ahead of their\n"
 f"surroundings in >= 40% of seasons since 2018 ({int(((vsum>=4)&(cov>=6)).sum())} cells early in >= 4 of >= 6\n"
 f"seasons; chance gives ~13). Routes, boundary burns and village rings all live here.\n\n"
 f"Not drawn on purpose: arrows. As a population the chains beat chance 2:1; any single\n"
 f"chain's direction is still a coin toss (FDR ~0.5). Two more seasons will fix that.")
fig.text(0.715,0.04,txt,color='#ddd',fontsize=9.3,family='monospace',va='bottom',ha='left',linespacing=1.35,
         bbox=dict(facecolor='#15171c',edgecolor='#333',boxstyle='round,pad=0.8'))
plt.savefig("/tmp/fx/xsa_render.png",dpi=72,facecolor=BG)
plt.savefig("/tmp/fx/xsa_render_hi.png",dpi=130,facecolor=BG)
print("ok")
