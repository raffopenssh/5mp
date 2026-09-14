import numpy as np, json, datetime, matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.colors as mc
from matplotlib.collections import LineCollection
from scipy import ndimage
d0=datetime.date(2020,1,1)
def D(s): return (datetime.date.fromisoformat(s)-d0).days
A=D("2024-08-01"); EXT=[22.7,31.3,4.25,11.0]; res=0.025
reg=np.load("/tmp/fx/reg_2024.npy"); ny,nx=reg.shape
fill=np.where(np.isnan(reg),0,reg); w=(~np.isnan(reg)).astype(float)
regs=ndimage.gaussian_filter(fill,10)/np.maximum(ndimage.gaussian_filter(w,10),1e-6); regs[ndimage.gaussian_filter(w,10)<0.5]=np.nan
def reg_at(lon,lat):
    ix=np.clip(((np.asarray(lon)-EXT[0])/res).astype(int),0,nx-1); iy=np.clip(((np.asarray(lat)-EXT[2])/res).astype(int),0,ny-1); return regs[iy,ix]
v=np.load("/tmp/fx/vis_2024.npz"); V=v['V']; vres=float(v['res'])
def vis(d,lon,lat):
    if d<0 or d>=V.shape[0]: return np.nan
    return V[d,min(int((lat-EXT[2])/vres),V.shape[1]-1),min(int((lon-EXT[0])/vres),V.shape[2]-1)]
lines=json.load(open("/tmp/fx/lines_2024.json"))
chains=json.load(open("/tmp/fx/v3_KF_seed_gap3_groups.json"))
parks=json.load(open("/tmp/fx/parks.json"))
aoi=json.loads(__import__('sqlite3').connect("file:/home/exedev/5mp/db.sqlite3?mode=ro",uri=True).execute("select geometry from aois where id='XSA_Study_Area'").fetchone()[0])

# per-chain: per-point lead, cloud-cut end?
for g in chains:
    tr=g['trajectory']; lon=np.array([p[0] for p in tr]); lat=np.array([p[1] for p in tr]); dd=np.array([D(p[2])-A for p in tr])
    g['plead']=reg_at(lon,lat)-dd; g['lon'],g['lat'],g['dd']=lon,lat,dd
    e=dd[-1]; vn=np.nanmean([vis(e+k,lon[-1],lat[-1]) for k in (1,2,3)])
    g['cloud_end']=bool(vn<0.35)
    g['gaps']=np.diff(dd)
n_cloud=sum(g['cloud_end'] for g in chains); n_into=sum(1 for g in chains if np.nanmin(g['plead'])<5)
n_drawn=sum(1 for g in chains if g['days']>=3 and g['distance_km']>=10)
print(f"chains {len(chains)}, ending under cloud {n_cloud} ({100*n_cloud/len(chains):.0f}%), followed into the arriving season {n_into} ({100*n_into/len(chains):.0f}%)")

BG='#0b0c10'
fig=plt.figure(figsize=(26,15),facecolor=BG)
ax=fig.add_axes([0.01,0.02,0.70,0.95],facecolor=BG)
def draw(ax,zoom=False,lw_scale=1.0):
    ax.set_facecolor(BG)
    for p in parks:
        try:
            gg=p['geometry'] if 'geometry' in p else p
            polys=gg['coordinates'] if gg['type']=='Polygon' else [q for mp in gg['coordinates'] for q in mp]
            for ring in ([polys[0]] if gg['type']=='Polygon' else polys):
                r=np.array(ring if gg['type']!='Polygon' else ring); 
                if r.ndim==3: r=r[0]
                ax.fill(r[:,0],r[:,1],color='#1d3a2a',alpha=0.55,lw=0); ax.plot(r[:,0],r[:,1],color='#3fbf6f',lw=0.7*lw_scale,alpha=0.8)
        except Exception: pass
    ac=np.array(aoi['coordinates'][0] if aoi['type']=='Polygon' else aoi['coordinates'][0][0]); ax.plot(ac[:,0],ac[:,1],'--',color='#6aa7ff',lw=1.0*lw_scale)
    # the lines as the app draws them today (red hairlines)
    segs=[np.array(L['coords'])[:,:2] for L in lines if len(L['coords'])>1]
    ax.add_collection(LineCollection(segs,colors='#ff3b1f',linewidths=0.35*lw_scale,alpha=0.30 if not zoom else 0.45))
    # auto-vanguard chains: colour = days ahead of the season at each point (white-yellow far ahead -> orange -> red as the season arrives)
    cmap=mc.LinearSegmentedColormap.from_list('lead',['#ff5a2a','#ffb53a','#fff29a','#ffffff']); norm=mc.Normalize(0,30)
    for g in chains:
        if len(g['lon'])<2 or g['days']<3 or g['distance_km']<10: continue
        pts=np.c_[g['lon'],g['lat']]; seg=np.stack([pts[:-1],pts[1:]],1); pl=(g['plead'][:-1]+g['plead'][1:])/2
        keep=pl>=0          # draw the chain only until the season catches it; from there it is part of the red mass
        if not keep.any(): continue
        wide=g['evidence_tier'] in ('supported','weak')
        cols=cmap(norm(pl[keep])); cols[:,3]=np.clip(0.45+pl[keep]/20,0.45,0.95)
        ax.add_collection(LineCollection(seg[keep],colors=cols,linewidths=(2.6 if wide else 1.6)*lw_scale,capstyle='round',
                                         linestyles=[('-' if gp<=1 else (0,(2,1.5))) for gp in np.asarray(g['gaps'])[keep]]))   # dashed = the day in between was not seen
        ax.plot(g['lon'][0],g['lat'][0],'o',ms=3.2*lw_scale,color='white',mec='none',alpha=0.9)   # where it began
        if g['cloud_end'] and g['plead'][-1]>=0:
            ax.plot(g['lon'][-1],g['lat'][-1],'o',ms=7*lw_scale,mfc='none',mec='#9ad0ff',mew=1.1*lw_scale,alpha=0.95)  # lost under cloud
    return cmap,norm
cmap,norm=draw(ax)
ax.set_xlim(EXT[0]-0.1,EXT[1]+0.1); ax.set_ylim(EXT[2]-0.1,EXT[3]+0.1); ax.set_aspect(1/np.cos(np.radians(7.6))); ax.axis('off')
ax.text(0.01,0.985,"XSA 2024/25 — fires that ran AHEAD of the season, followed into it",transform=ax.transAxes,color='#e8e8e8',fontsize=17,va='top',fontweight='bold')
# zoom
ZX=[23.2,26.2]; ZY=[6.4,8.9]
ax.plot([ZX[0],ZX[1],ZX[1],ZX[0],ZX[0]],[ZY[0],ZY[0],ZY[1],ZY[1],ZY[0]],color='#cccccc',lw=0.8,alpha=0.7)
az=fig.add_axes([0.715,0.44,0.275,0.53],facecolor=BG); draw(az,zoom=True,lw_scale=1.6)
az.set_xlim(*ZX); az.set_ylim(*ZY); az.set_aspect(1/np.cos(np.radians(7.6))); az.set_xticks([]); az.set_yticks([])
for s in az.spines.values(): s.set_color('#555')
az.set_title("detail",color='#ddd',fontsize=12,loc='left')
# legend, in plain English
lg=fig.add_axes([0.715,0.02,0.275,0.335],facecolor='#14161c'); lg.axis('off')
sm=plt.cm.ScalarMappable(cmap=cmap,norm=norm); cb=fig.add_axes([0.735,0.385,0.22,0.014]); cbar=plt.colorbar(sm,cax=cb,orientation='horizontal')
cbar.set_ticks([0,10,20,30]); cbar.set_ticklabels(['season arrives','10 d ahead','20 d','30+ d ahead']); cb.set_title('line colour: how far ahead of the season the fire was',color='#ddd',fontsize=9.5,loc='left'); cbar.ax.tick_params(colors='#ddd',labelsize=9); cbar.outline.set_edgecolor('#555')
txt=(f"HOW TO READ\n\n"
 f"Thin red lines — every fire line of the season, exactly as the app draws them today\n"
 f"({len(lines):,}). They show WHERE fire runs; inside the burning season the\n"
 f"day-to-day order along them is not recoverable (measured against shuffled days).\n\n"
 f"Bright lines — AUTO-VANGUARD chains ({n_drawn} of {len(chains):,} with >=3 days, >=10 km). Each began in country that had not\n"
 f"burned yet, at least 10 days before the season reached it (white dot = start), and\n"
 f"was then followed day by day with a Kalman filter — a tight search window around\n"
 f"where the fire was heading — into the arriving season. Colour = how far ahead of\n"
 f"the season the fire was at that point; where the season catches up the chain is\n"
 f"no longer drawn — from there it is one more red line in the mass.\n"
 f"Wide = day order confirmed (evidence tier weak/supported), thin = unconfirmed.\n\n"
 f"Dashed segment — a day in between was not seen (cloud, or a fire too small for the\n"
 f"satellite). Up to 2 missed days are bridged, as today. Coasting further through\n"
 f"cloud was tested and REJECTED: it links shuffled days as readily as real ones.\n\n"
 f"Blue ring — the chain was lost under cloud ({n_cloud}, {100*n_cloud/len(chains):.0f}% of chains): the sky over\n"
 f"it was not seen for the following days. We do not guess where it went.\n\n"
 f"Skill (real vs day-shuffled): links 11,822 vs 8,388; km per chain 76 vs 48;\n"
 f"fires in ≥150 km chains 24,567 vs 9,714. Same-day-to-next-day links 8,153 vs 4,728.")
lg.text(0.03,0.97,txt,transform=lg.transAxes,color='#d8d8d8',fontsize=8.6,va='top',family='monospace',linespacing=1.3)
plt.savefig("/tmp/fx/www/xsa_autovanguard_2024.png",dpi=90,facecolor=BG)
plt.savefig("/tmp/fx/www/xsa_autovanguard_2024_small.png",dpi=48,facecolor=BG)
print("ok")
