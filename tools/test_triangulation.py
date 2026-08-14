import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from citygml2tiles import triangulate, geodetic_to_ecef, _newell_normal

def area3d(v, tris):
    s=0.0
    for a,b,c in tris:
        s += 0.5*np.linalg.norm(np.cross(v[b]-v[a], v[c]-v[a]))
    return s

def mkring(pts_xy, z=0.0):
    # 平面上の点をローカル m -> 緯度経度に変換して ECEF へ
    lat0, lon0 = 35.6745, 139.7592
    out=[]
    for x,y in pts_xy:
        lat = lat0 + y/111320.0
        lon = lon0 + x/(111320.0*math.cos(math.radians(lat0)))
        out.append((lat,lon,z))
    a=np.array(out)
    return geodetic_to_ecef(a[:,0],a[:,1],a[:,2])

# 1) 凸四角形
sq=[(0,0),(40,0),(40,30),(0,30)]
v,t = triangulate(mkring(sq), [])
print("凸四角形: tris=%d area=%.1f (期待 1200)"%(len(t), area3d(v,t)))
assert len(t)==2 and abs(area3d(v,t)-1200)<5

# 2) 凹L字（ear clipping が必要）
L=[(0,0),(60,0),(60,20),(20,20),(20,50),(0,50)]
v,t = triangulate(mkring(L), [])
print("凹L字   : tris=%d area=%.1f (期待 1800)"%(len(t), area3d(v,t)))
assert abs(area3d(v,t)-1800)<10

# 3) 穴あき（中庭）
outer=[(0,0),(60,0),(60,60),(0,60)]
hole=[(20,20),(40,20),(40,40),(20,40)]
v,t = triangulate(mkring(outer), [mkring(hole)])
print("穴あき  : tris=%d area=%.1f (期待 3200)"%(len(t), area3d(v,t)))
assert abs(area3d(v,t)-3200)<25

# 4) 時計回り入力でも法線が正しく上を向くか
cw=[(0,0),(0,30),(40,30),(40,0)]
v,t = triangulate(mkring(cw), [])
n=_newell_normal(v)
up = geodetic_to_ecef(np.array([35.6745]),np.array([139.7592]),np.array([1.0]))[0]-geodetic_to_ecef(np.array([35.6745]),np.array([139.7592]),np.array([0.0]))[0]
print("時計回り: tris=%d area=%.1f 法線·上=%.3f"%(len(t), area3d(v,t), float(np.dot(n,up))))
assert abs(area3d(v,t)-1200)<5
print("[OK] 三角形分割テスト 4件すべて合格")
