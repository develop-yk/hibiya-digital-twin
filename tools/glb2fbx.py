"""Blender をヘッドレスで走らせ、glb を FBX / OBJ に書き出す。

  blender -b -P glb2fbx.py -- in.glb out.fbx out.obj
"""
import os
import sys

import bpy

argv = sys.argv[sys.argv.index('--') + 1:]
src, fbx, obj = argv[0], argv[1], argv[2]

# 起動時の Cube / Light / Camera を消す
bpy.ops.wm.read_factory_settings(use_empty=True)

# 単位をメートルに固定（FBX 側の縮尺ずれを防ぐ）
sc = bpy.context.scene
sc.unit_settings.system = 'METRIC'
sc.unit_settings.scale_length = 1.0

print('>>> import', src, flush=True)
bpy.ops.import_scene.gltf(filepath=src)

n_v = n_t = 0
for o in bpy.context.scene.objects:
    if o.type != 'MESH':
        continue
    o.data.polygons.foreach_set('use_smooth', [False] * len(o.data.polygons))
    n_v += len(o.data.vertices)
    n_t += len(o.data.loop_triangles) or len(o.data.polygons)
    print('   %-24s verts=%d faces=%d' % (o.name, len(o.data.vertices),
                                          len(o.data.polygons)), flush=True)
print('>>> total verts=%d' % n_v, flush=True)

print('>>> export FBX', flush=True)
bpy.ops.export_scene.fbx(
    filepath=fbx,
    use_selection=False,
    apply_unit_scale=True,
    global_scale=1.0,
    apply_scale_options='FBX_SCALE_NONE',
    object_types={'MESH'},
    use_mesh_modifiers=False,
    mesh_smooth_type='FACE',
    use_triangles=True,
    add_leaf_bones=False,
    bake_anim=False,
    path_mode='AUTO',
    axis_forward='-Z',
    axis_up='Y',
)

print('>>> export OBJ', flush=True)
bpy.ops.wm.obj_export(
    filepath=obj,
    export_selected_objects=False,
    apply_modifiers=False,
    export_materials=True,
    export_normals=True,
    export_uv=False,
    export_triangulated_mesh=True,
    forward_axis='NEGATIVE_Z',
    up_axis='Y',
)

for p in (fbx, obj):
    if os.path.exists(p):
        print('>>> %s  %.1f MB' % (p, os.path.getsize(p) / 1048576), flush=True)
