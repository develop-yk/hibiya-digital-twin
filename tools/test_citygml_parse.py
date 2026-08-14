import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lxml import etree
from citygml2tiles import pick_lod_element, collect_polygons, building_attributes, local

XML = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"
 xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:gen="http://www.opengis.net/citygml/generics/2.0"
 xmlns:gml="http://www.opengis.net/gml">
 <core:cityObjectMember>
  <bldg:Building gml:id="bldg_lod2_x">
   <bldg:measuredHeight uom="m">31.4</bldg:measuredHeight>
   <bldg:storeysAboveGround>8</bldg:storeysAboveGround>
   <bldg:yearOfConstruction>1998</bldg:yearOfConstruction>
   <bldg:usage>411</bldg:usage>
   <bldg:lod1Solid><gml:Solid><gml:exterior><gml:CompositeSurface>
     <gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>
      <gml:posList srsDimension="3">35.6745 139.7592 2 35.6745 139.7594 2 35.6747 139.7594 2 35.6747 139.7592 2 35.6745 139.7592 2</gml:posList>
     </gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>
   </gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod1Solid>
   <bldg:boundedBy><bldg:RoofSurface gml:id="r1">
     <bldg:lod2MultiSurface><gml:MultiSurface>
       <gml:surfaceMember><gml:Polygon>
         <gml:exterior><gml:LinearRing><gml:posList srsDimension="3">
           35.6745 139.7592 33 35.6745 139.7594 33 35.6747 139.7594 33 35.6747 139.7592 33 35.6745 139.7592 33
         </gml:posList></gml:LinearRing></gml:exterior>
         <gml:interior><gml:LinearRing><gml:posList srsDimension="3">
           35.67455 139.75925 33 35.67455 139.75935 33 35.67465 139.75935 33 35.67465 139.75925 33 35.67455 139.75925 33
         </gml:posList></gml:LinearRing></gml:interior>
       </gml:Polygon></gml:surfaceMember>
     </gml:MultiSurface></bldg:lod2MultiSurface>
   </bldg:RoofSurface></bldg:boundedBy>
  </bldg:Building>
 </core:cityObjectMember>
</core:CityModel>"""

root = etree.fromstring(XML.encode())
b = [e for e in root.iter() if local(e.tag)=="Building"][0]
els, lod = pick_lod_element(b)
polys = []
for e in els: polys.extend(collect_polygons(e))
attrs = building_attributes(b)
print("選択LOD:", lod, "/ ポリゴン数:", len(polys), "/ 外周点数:", len(polys[0][0]), "/ 穴数:", len(polys[0][1]))
print("属性:", attrs)
assert lod == 2, "LOD2 が優先されていない"
assert len(polys) == 1 and len(polys[0][1]) == 1, "穴あきポリゴンが取れていない"
assert len(polys[0][0]) == 4, "LinearRing の閉点が除去されていない"
assert attrs["height"] == 31.4 and attrs["storeys"] == 8 and attrs["usage"] == "411" and attrs["year"] == 1998
print("[OK] LOD2 / boundedBy / 穴あき / 属性の抽出テスト合格")
