import bpy, re, time
from bpy.props import *
from mathutils import *
from .common import *
from .subtree import *
from .node_connections import *
from .node_arrangements import *
from . import lib, Layer, Mask, ImageAtlas

BL28_HACK = True

def remember_before_bake(self, context, yp):
    scene = self.scene
    obj = self.obj
    uv_layers = self.uv_layers
    ypui = context.window_manager.ypui

    # Remember render settings
    self.ori_engine = scene.render.engine
    self.ori_bake_type = scene.cycles.bake_type
    self.ori_samples = scene.cycles.samples
    self.ori_threads_mode = scene.render.threads_mode
    self.ori_margin = scene.render.bake.margin
    self.ori_use_clear = scene.render.bake.use_clear

    # Remember uv
    #self.ori_active_uv = uv_layers.active
    self.ori_active_uv = uv_layers.active.name

    # Remember scene objects
    if bpy.app.version_string.startswith('2.8'):
        self.ori_active_selected_objs = [o for o in scene.objects if o.select_get()]
    else: self.ori_active_selected_objs = [o for o in scene.objects if o.select]

    # Remember world settings
    if bpy.app.version_string.startswith('2.8') and scene.world:
        self.ori_distance = scene.world.light_settings.distance

    # Remember ypui
    #self.ori_disable_temp_uv = ypui.disable_auto_temp_uv_update

    # Remember yp
    self.parallax_ch = get_root_parallax_channel(yp)
    #self.use_parallax = True if parallax_ch else False

def prepare_bake_settings(self, context, yp):
    scene = self.scene
    obj = self.obj
    uv_layers = self.uv_layers
    ypui = context.window_manager.ypui

    scene.render.engine = 'CYCLES'
    scene.cycles.bake_type = 'EMIT'
    scene.cycles.samples = self.samples
    scene.render.threads_mode = 'AUTO'
    scene.render.bake.margin = self.margin
    #scene.render.bake.use_clear = True
    scene.render.bake.use_clear = False

    # Disable other object selections and select only active object
    if bpy.app.version_string.startswith('2.8'):
        #for o in scene.objects:
        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
    else:
        for o in scene.objects:
            o.select = False
        obj.select = True

    # Set active uv layers
    uv_layers.active = uv_layers.get(self.uv_map)

    # Disable auto temp uv update
    #ypui.disable_auto_temp_uv_update = True

    # Disable parallax channel
    #parallax_ch = get_root_parallax_channel(yp)
    if self.parallax_ch:
        self.parallax_ch.enable_parallax = False

def recover_bake_settings(self, context, yp):
    scene = self.scene
    obj = self.obj
    uv_layers = self.uv_layers
    ypui = context.window_manager.ypui

    scene.render.engine = self.ori_engine
    scene.cycles.bake_type = self.ori_bake_type
    scene.cycles.samples = self.ori_samples
    scene.render.threads_mode = self.ori_threads_mode
    scene.render.bake.margin = self.ori_margin
    scene.render.bake.use_clear = self.ori_use_clear

    # Recover world settings
    if bpy.app.version_string.startswith('2.8') and scene.world:
        scene.world.light_settings.distance = self.ori_distance

    # Recover uv
    uv_layers.active = uv_layers.get(self.ori_active_uv)

    #return

    # Disable other object selections
    if bpy.app.version_string.startswith('2.8'):
        #for o in scene.objects:
        for o in context.view_layer.objects:
            if o in self.ori_active_selected_objs:
                o.select_set(True)
            else: o.select_set(False)
    else:
        for o in scene.objects:
            if o in self.ori_active_selected_objs:
                o.select = True
            else: o.select = False

    # Recover active object
    #scene.objects.active = self.ori_active_obj

    # Recover ypui
    #ypui.disable_auto_temp_uv_update = self.ori_disable_temp_uv

    # Recover parallax
    if self.parallax_ch:
        self.parallax_ch.enable_parallax = True

def transfer_uv(obj, mat, entity, uv_map):

    if hasattr(obj.data, 'uv_textures'):
        uv_layers = obj.data.uv_textures
    else: uv_layers = obj.data.uv_layers

    uv_layers.active = uv_layers.get(uv_map)

    # Check entity
    m1 = re.match(r'^yp\.layers\[(\d+)\]$', entity.path_from_id())
    m2 = re.match(r'^yp\.layers\[(\d+)\]\.masks\[(\d+)\]$', entity.path_from_id())

    if m1: 
        source = get_layer_source(entity)
        mapping = get_layer_mapping(entity)
    elif m2: 
        source = get_mask_source(entity)
        mapping = get_mask_mapping(entity)
    else: return

    image = source.image
    if not image: return

    # Get image settings
    segment = None
    use_alpha = False
    if image.yia.is_image_atlas and entity.segment_name != '':
        segment = image.yia.segments.get(entity.segment_name)
        width = segment.width
        height = segment.height
        if image.yia.color == 'WHITE':
            col = (1.0, 1.0, 1.0, 1.0)
        elif image.yia.color == 'BLACK':
            col = (0.0, 0.0, 0.0, 1.0)
        else: 
            col = (0.0, 0.0, 0.0, 0.0)
            use_alpha = True
    else:
        width = image.size[0]
        height = image.size[1]
        # Change color if baked image is found
        if 'Pointiness' in image.name:
            col = (0.73, 0.73, 0.73, 1.0)
        elif 'AO' in image.name:
            col = (1.0, 1.0, 1.0, 1.0)
        else:
            col = (0.0, 0.0, 0.0, 0.0)
            use_alpha = True

    # Create temp image as bake target
    temp_image = bpy.data.images.new(name='__TEMP',
            width=width, height=height, alpha=True, float_buffer=image.is_float)
    temp_image.colorspace_settings.name = 'Linear'
    temp_image.generated_color = col

    # Create bake nodes
    tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
    emit = mat.node_tree.nodes.new('ShaderNodeEmission')

    # Set image to temp nodes
    src = mat.node_tree.nodes.new('ShaderNodeTexImage')
    src.image = image
    src_uv = mat.node_tree.nodes.new('ShaderNodeUVMap')
    src_uv.uv_map = entity.uv_name

    # Copy mapping
    mapp = mat.node_tree.nodes.new('ShaderNodeMapping')

    mapp.translation[0] = mapping.translation[0]
    mapp.translation[1] = mapping.translation[1]
    mapp.translation[2] = mapping.translation[2]

    mapp.rotation[0] = mapping.rotation[0]
    mapp.rotation[1] = mapping.rotation[1]
    mapp.rotation[2] = mapping.rotation[2]

    mapp.scale[0] = mapping.scale[0]
    mapp.scale[1] = mapping.scale[1]
    mapp.scale[2] = mapping.scale[2]

    # Get material output
    output = get_active_mat_output_node(mat.node_tree)
    ori_bsdf = output.inputs[0].links[0].from_socket

    straight_over = None
    if use_alpha:
        straight_over = mat.node_tree.nodes.new('ShaderNodeGroup')
        straight_over.node_tree = get_node_tree_lib(lib.STRAIGHT_OVER)
        straight_over.inputs[1].default_value = 0.0

    # Set temp image node
    tex.image = temp_image
    mat.node_tree.nodes.active = tex

    # Links
    mat.node_tree.links.new(src_uv.outputs[0], mapp.inputs[0])
    mat.node_tree.links.new(mapp.outputs[0], src.inputs[0])
    rgb = src.outputs[0]
    alpha = src.outputs[1]
    if straight_over:
        mat.node_tree.links.new(rgb, straight_over.inputs[2])
        mat.node_tree.links.new(alpha, straight_over.inputs[3])
        rgb = straight_over.outputs[0]

    mat.node_tree.links.new(rgb, emit.inputs[0])
    mat.node_tree.links.new(emit.outputs[0], output.inputs[0])

    # Bake!
    bpy.ops.object.bake()

    # Copy results to original image
    target_pxs = list(image.pixels)
    temp_pxs = list(temp_image.pixels)

    if segment:
        start_x = width * segment.tile_x
        start_y = height * segment.tile_y
    else:
        start_x = 0
        start_y = 0

    for y in range(height):
        temp_offset_y = width * 4 * y
        offset_y = image.size[0] * 4 * (y + start_y)
        for x in range(width):
            temp_offset_x = 4 * x
            offset_x = 4 * (x + start_x)
            for i in range(3):
                target_pxs[offset_y + offset_x + i] = temp_pxs[temp_offset_y + temp_offset_x + i]

    # Bake alpha if using alpha
    #srgb2lin = None
    if use_alpha:
        #srgb2lin = mat.node_tree.nodes.new('ShaderNodeGroup')
        #srgb2lin.node_tree = get_node_tree_lib(lib.SRGB_2_LINEAR)

        #mat.node_tree.links.new(src.outputs[1], srgb2lin.inputs[0])
        #mat.node_tree.links.new(srgb2lin.outputs[0], emit.inputs[0])
        mat.node_tree.links.new(src.outputs[1], emit.inputs[0])

        # Bake again!
        bpy.ops.object.bake()

        temp_pxs = list(temp_image.pixels)

        for y in range(height):
            temp_offset_y = width * 4 * y
            offset_y = image.size[0] * 4 * (y + start_y)
            for x in range(width):
                temp_offset_x = 4 * x
                offset_x = 4 * (x + start_x)
                target_pxs[offset_y + offset_x + 3] = temp_pxs[temp_offset_y + temp_offset_x]

    # Copy back edited pixels to original image
    image.pixels = target_pxs

    # Remove temp nodes
    simple_remove_node(mat.node_tree, tex)
    simple_remove_node(mat.node_tree, emit)
    simple_remove_node(mat.node_tree, src)
    simple_remove_node(mat.node_tree, src_uv)
    simple_remove_node(mat.node_tree, mapp)
    if straight_over:
        simple_remove_node(mat.node_tree, straight_over)
    #if srgb2lin:
    #    simple_remove_node(mat.node_tree, srgb2lin)

    mat.node_tree.links.new(ori_bsdf, output.inputs[0])

    # Update entity transform
    entity.translation = (0.0, 0.0, 0.0)
    entity.rotation = (0.0, 0.0, 0.0)
    entity.scale = (1.0, 1.0, 1.0)

    # Change uv of entity
    entity.uv_name = uv_map

def bake_channel(width, height, uv_map, mat, node, ch):

    tree = node.node_tree
    yp = tree.yp

    # Create setup nodes
    tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
    emit = mat.node_tree.nodes.new('ShaderNodeEmission')

    if ch.type == 'NORMAL':
        norm = mat.node_tree.nodes.new('ShaderNodeGroup')
        norm.node_tree = get_node_tree_lib(lib.BAKE_NORMAL)

        t = norm.node_tree.nodes.get('_tangent')
        t.uv_map = uv_map
        
        bt = norm.node_tree.nodes.get('_bitangent')
        bt.uv_map = uv_map

        if BL28_HACK:
        #if bpy.app.version_string.startswith('2.8'):
            bake_uv = yp.uvs.get(uv_map)
            if bake_uv:
                tangent_process = tree.nodes.get(bake_uv.tangent_process)
                t_socket = t.outputs[0].links[0].to_socket
                bt_socket = bt.outputs[0].links[0].to_socket
                hack_bt = norm.node_tree.nodes.new('ShaderNodeGroup')
                hack_bt.node_tree = tangent_process.node_tree
                hack_bt.inputs['Backface Always Up'].default_value = 1.0 if yp.enable_backface_always_up else 0.0
                hack_bt.inputs['Blender 2.8 Cycles Hack'].default_value = 1.0
                create_link(norm.node_tree, hack_bt.outputs['Tangent'], t_socket)
                create_link(norm.node_tree, hack_bt.outputs['Bitangent'], bt_socket)

    # Set tex as active node
    mat.node_tree.nodes.active = tex

    # Get output node and remember original bsdf input
    output = get_active_mat_output_node(mat.node_tree)
    ori_bsdf = output.inputs[0].links[0].from_socket

    # Connect emit to output material
    mat.node_tree.links.new(emit.outputs[0], output.inputs[0])

    # Set nodes
    baked = tree.nodes.get(ch.baked)
    if not baked:
        baked = new_node(tree, ch, 'baked', 'ShaderNodeTexImage', 'Baked ' + ch.name)
    if hasattr(baked, 'color_space'):
        if ch.colorspace == 'LINEAR' or ch.type == 'NORMAL':
            baked.color_space = 'NONE'
        else: baked.color_space = 'COLOR'
    
    # Normal related nodes
    if ch.type == 'NORMAL':
        baked_normal = tree.nodes.get(ch.baked_normal)
        if not baked_normal:
            baked_normal = new_node(tree, ch, 'baked_normal', 'ShaderNodeNormalMap', 'Baked Normal')
        baked_normal.uv_map = uv_map

        baked_normal_prep = tree.nodes.get(ch.baked_normal_prep)
        if not baked_normal_prep:
            baked_normal_prep = new_node(tree, ch, 'baked_normal_prep', 'ShaderNodeGroup', 
                    'Baked Normal Preparation')
            baked_normal_prep.node_tree = get_node_tree_lib(lib.NORMAL_MAP_PREP)

    # Baked image names
    img_name = tree.name + ' ' + ch.name
    filepath = ''

    # Check if image is available
    if baked.image:
        img_name = baked.image.name
        filepath = baked.image.filepath
        baked.image.name = '____TEMP'
        #if baked.image.users == 1:
        #    bpy.data.images.remove(baked.image)

    #Create new image
    img = bpy.data.images.new(name=img_name,
            width=width, height=height, alpha=True) #, alpha=True, float_buffer=hdr)
    img.generated_type = 'BLANK'
    if hasattr(img, 'use_alpha'):
        img.use_alpha = True
    if ch.type == 'NORMAL':
        img.generated_color = (0.5, 0.5, 1.0, 1.0)
    elif ch.type == 'VALUE':
        #val = node.inputs[ch.io_index].default_value
        val = node.inputs[ch.name].default_value
        img.generated_color = (val, val, val, 1.0)
    elif ch.enable_alpha:
        img.generated_color = (0.0, 0.0, 0.0, 1.0)
    else:
        #col = node.inputs[ch.io_index].default_value
        col = node.inputs[ch.name].default_value
        col = Color((col[0], col[1], col[2]))
        col = linear_to_srgb(col)
        img.generated_color = (col.r, col.g, col.b, 1.0)

    # Set filepath
    if filepath != '':
        img.filepath = filepath

    # Set image to tex node
    tex.image = img

    # Links to bake
    #rgb = node.outputs[ch.io_index]
    rgb = node.outputs[ch.name]
    if ch.type == 'NORMAL':
        rgb = create_link(mat.node_tree, rgb, norm.inputs[0])[0]
    if ch.colorspace == 'LINEAR' or ch.type == 'NORMAL':
        #if bpy.app.version_string.startswith('2.8'):
        img.colorspace_settings.name = 'Linear'
        #if not bpy.app.version_string.startswith('2.8'):
        #    rgb = create_link(mat.node_tree, rgb, srgb2lin.inputs[0])[0]
    mat.node_tree.links.new(rgb, emit.inputs[0])

    # Bake!
    bpy.ops.object.bake()

    # Bake alpha
    if ch.type == 'RGB' and ch.enable_alpha:
        # Create temp image
        alpha_img = bpy.data.images.new(name='__TEMP__', width=width, height=height) 
        alpha_img.colorspace_settings.name = 'Linear'
        create_link(mat.node_tree, node.outputs[ch.name + io_suffix['ALPHA']], emit.inputs[0])

        tex.image = alpha_img

        # Bake
        bpy.ops.object.bake()

        # Copy alpha pixels to main image alpha channel
        img_pxs = list(img.pixels)
        alp_pxs = list(alpha_img.pixels)

        for y in range(height):
            offset_y = width * 4 * y
            for x in range(width):
                a = alp_pxs[offset_y + (x*4)]
                #a = srgb_to_linear_per_element(a)
                img_pxs[offset_y + (x*4) + 3] = a

        img.pixels = img_pxs

        # Remove temp image
        bpy.data.images.remove(alpha_img)

    # Bake displacement
    if ch.type == 'NORMAL': # and ch.enable_parallax:

        ### Normal overlay only

        baked_normal_overlay = tree.nodes.get(ch.baked_normal_overlay)
        if not baked_normal_overlay:
            baked_normal_overlay = new_node(tree, ch, 'baked_normal_overlay', 'ShaderNodeTexImage', 
                    'Baked ' + ch.name + ' Overlay Only')
            if hasattr(baked_normal_overlay, 'color_space'):
                baked_normal_overlay.color_space = 'NONE'

        if baked_normal_overlay.image:
            norm_img_name = baked_normal_overlay.image.name
            filepath = baked_normal_overlay.image.filepath
            baked_normal_overlay.image.name = '____NORM_TEMP'
        else:
            norm_img_name = tree.name + ' ' + ch.name + ' Overlay Only'

        # Create target image
        norm_img = bpy.data.images.new(name=norm_img_name, width=width, height=height) 
        norm_img.generated_color = (0.5, 0.5, 1.0, 1.0)
        norm_img.colorspace_settings.name = 'Linear'

        # Bake setup (doing little bit doing hacky reconnection here)
        end = tree.nodes.get(TREE_END)
        ori_soc = end.inputs[ch.name].links[0].from_socket
        end_linear = tree.nodes.get(ch.end_linear)
        soc = end_linear.inputs['Normal Overlay'].links[0].from_socket
        create_link(tree, soc, end.inputs[ch.name])
        #create_link(mat.node_tree, node.outputs[ch.name], emit.inputs[0])

        tex.image = norm_img

        # Bake
        bpy.ops.object.bake()

        # Recover connection
        create_link(tree, ori_soc, end.inputs[ch.name])

        # Set baked normal overlay image
        if baked_normal_overlay.image:
            temp = baked_normal_overlay.image
            img_users = get_all_image_users(baked_normal_overlay.image)
            for user in img_users:
                user.image = norm_img
            bpy.data.images.remove(temp)
        else:
            baked_normal_overlay.image = norm_img

        ### Displacement

        baked_disp = tree.nodes.get(ch.baked_disp)
        if not baked_disp:
            baked_disp = new_node(tree, ch, 'baked_disp', 'ShaderNodeTexImage', 
                    'Baked ' + ch.name + ' Displacement')
            if hasattr(baked_disp, 'color_space'):
                baked_disp.color_space = 'NONE'

        if baked_disp.image:
            disp_img_name = baked_disp.image.name
            filepath = baked_disp.image.filepath
            baked_disp.image.name = '____DISP_TEMP'
        else:
            disp_img_name = tree.name + ' ' + ch.name + ' Displacement'

        # Create target image
        disp_img = bpy.data.images.new(name=disp_img_name, width=width, height=height) 
        disp_img.generated_color = (0.5, 0.5, 0.5, 1.0)
        disp_img.colorspace_settings.name = 'Linear'

        # Bake setup
        create_link(mat.node_tree, node.outputs[ch.name + io_suffix['HEIGHT']], emit.inputs[0])
        tex.image = disp_img

        # Bake
        bpy.ops.object.bake()

        # Set baked displacement image
        if baked_disp.image:
            temp = baked_disp.image
            img_users = get_all_image_users(baked_disp.image)
            for user in img_users:
                user.image = disp_img
            bpy.data.images.remove(temp)
        else:
            baked_disp.image = disp_img

    # Set image to baked node and replace all previously original users
    if baked.image:
        temp = baked.image
        img_users = get_all_image_users(baked.image)
        for user in img_users:
            user.image = img
        bpy.data.images.remove(temp)
    else:
        baked.image = img

    simple_remove_node(mat.node_tree, tex)
    simple_remove_node(mat.node_tree, emit)
    if ch.type == 'NORMAL':
        simple_remove_node(mat.node_tree, norm)

    # Recover original bsdf
    mat.node_tree.links.new(ori_bsdf, output.inputs[0])

class YBakeToLayer(bpy.types.Operator):
    bl_idname = "node.y_bake_to_layer"
    bl_label = "Bake To Layer"
    bl_description = "Bake something as layer/mask"
    bl_options = {'REGISTER', 'UNDO'}

    name = StringProperty(default='')

    uv_map = StringProperty(default='')
    uv_map_coll = CollectionProperty(type=bpy.types.PropertyGroup)

    overwrite = BoolProperty(
            name='Overwrite available layer',
            description='Overwrite available layer',
            default=False
            )
    overwrite_name = StringProperty(default='')
    overwrite_coll = CollectionProperty(type=bpy.types.PropertyGroup)

    samples = IntProperty(name='Bake Samples', 
            description='Bake Samples, more means less jagged on generated textures', 
            default=1, min=1)

    margin = IntProperty(name='Bake Margin',
            description = 'Bake margin in pixels',
            default=5, subtype='PIXEL')

    type = EnumProperty(
            name = 'Bake Type',
            description = 'Bake Type',
            items = (('AO', 'Ambient Occlusion', ''),
                     ('POINTINESS', 'Pointiness', '')),
            default='AO'
            )

    ao_distance = FloatProperty(default=1.0)

    target_type = EnumProperty(
            name = 'Target Bake Type',
            description = 'Target Bake Type',
            items = (('LAYER', 'Layer', ''),
                     ('MASK', 'Mask', '')),
            default='LAYER'
            )

    width = IntProperty(name='Width', default = 1024, min=1, max=4096)
    height = IntProperty(name='Height', default = 1024, min=1, max=4096)

    channel_idx = EnumProperty(
            name = 'Channel',
            description = 'Channel of new layer, can be changed later',
            items = Layer.channel_items,
            update=Layer.update_channel_idx_new_layer)

    blend_type = EnumProperty(
        name = 'Blend',
        items = blend_type_items,
        default = 'MIX')

    normal_blend_type = EnumProperty(
            name = 'Normal Blend Type',
            items = normal_blend_items,
            default = 'MIX')

    normal_map_type = EnumProperty(
            name = 'Normal Map Type',
            description = 'Normal map type of this layer',
            items = Layer.get_normal_map_type_items)
            #default = 'NORMAL_MAP')

    hdr = BoolProperty(name='32 bit Float', default=True)

    use_baked_disp = BoolProperty(
            name='Use Baked Displacement Map',
            description='Use baked displacement map, this will also apply subdiv setup on object',
            default=False
            )

    @classmethod
    def poll(cls, context):
        return get_active_ypaint_node() and context.object.type == 'MESH'

    def invoke(self, context, event):
        obj = self.obj = context.object
        scene = self.scene = context.scene
        node = get_active_ypaint_node()
        yp = node.node_tree.yp

        # Default normal map type is bump
        self.normal_map_type = 'BUMP_MAP'

        # Use active uv layer name by default
        if hasattr(obj.data, 'uv_textures'):
            uv_layers = self.uv_layers = obj.data.uv_textures
        else: uv_layers = self.uv_layers = obj.data.uv_layers

        # UV Map collections update
        self.uv_map_coll.clear()
        for uv in uv_layers:
            if not uv.name.startswith(TEMP_UV):
                self.uv_map_coll.add().name = uv.name

        #if len(uv_layers) > 0:
            #active_name = uv_layers.active.name
            #if active_name == TEMP_UV:
            #    self.uv_map = yp.layers[yp.active_layer_index].uv_name
            #else: self.uv_map = uv_layers.active.name
        if len(self.uv_map_coll) > 0:
            self.uv_map = self.uv_map_coll[0].name

        # Set name
        mat = get_active_material()
        if self.type == 'AO':
            self.blend_type = 'MULTIPLY'
            suffix = 'AO'
            self.samples = 32
        else: 
            self.blend_type = 'ADD'
            suffix = 'Pointiness'
            self.samples = 1
        self.name = get_unique_name(mat.name + ' ' + suffix, bpy.data.images)

        # Clear overwrite_coll
        self.overwrite_coll.clear()

        # Get overwritable layers
        if self.target_type == 'LAYER':
            for layer in yp.layers:
                if layer.type == 'IMAGE':
                    source = get_layer_source(layer)
                    if source.image and suffix in source.image.name:
                        self.overwrite_coll.add().name = source.image.name

        # Get overwritable masks
        elif len(yp.layers) > 0:
            active_layer = yp.layers[yp.active_layer_index]
            for mask in active_layer.masks:
                if mask.type == 'IMAGE':
                    source = get_mask_source(mask)
                    if source.image and suffix in source.image.name:
                        self.overwrite_coll.add().name = source.image.name

        if len(self.overwrite_coll) > 0:
            self.overwrite = True
            self.overwrite_name = self.overwrite_coll[0].name
        else:
            self.overwrite = False

        return context.window_manager.invoke_props_dialog(self, width=320)

    def check(self, context):
        return True

    def draw(self, context):
        node = get_active_ypaint_node()
        yp = node.node_tree.yp

        channel = yp.channels[int(self.channel_idx)] if self.channel_idx != '-1' else None
        height_root_ch = get_root_height_channel(yp)

        if bpy.app.version_string.startswith('2.8'):
            row = self.layout.split(factor=0.4)
        else: row = self.layout.split(percentage=0.4)

        col = row.column(align=False)

        if len(self.overwrite_coll) > 0:
            col.label(text='Overwrite:')
        if len(self.overwrite_coll) > 0 and self.overwrite:
            if self.target_type == 'LAYER':
                col.label(text='Overwrite Layer:')
            else:
                col.label(text='Overwrite Mask:')
        else:
            col.label(text='Name:')

            if self.target_type == 'LAYER':
                col.label(text='Channel:')
                if channel and channel.type == 'NORMAL':
                    col.label(text='Type:')

        if self.type == 'AO':
            col.label(text='AO Distance:')

        col.label(text='')
        col.label(text='Width:')
        col.label(text='Height:')
        col.label(text='UV Map:')
        col.label(text='Samples:')
        col.label(text='Margin:')

        if height_root_ch:
            col.label(text='')

        col = row.column(align=False)

        if len(self.overwrite_coll) > 0:
            col.prop(self, 'overwrite', text='')

        if len(self.overwrite_coll) > 0 and self.overwrite:
            col.prop_search(self, "overwrite_name", self, "overwrite_coll", text='', icon='IMAGE_DATA')
        else:
            col.prop(self, 'name', text='')

            if self.target_type == 'LAYER':
                rrow = col.row(align=True)
                rrow.prop(self, 'channel_idx', text='')
                if channel:
                    if channel.type == 'NORMAL':
                        rrow.prop(self, 'normal_blend_type', text='')
                        col.prop(self, 'normal_map_type', text='')
                    else: 
                        rrow.prop(self, 'blend_type', text='')

        col.prop(self, 'ao_distance', text='')
        col.prop(self, 'hdr')
        col.prop(self, 'width', text='')
        col.prop(self, 'height', text='')
        col.prop_search(self, "uv_map", self, "uv_map_coll", text='', icon='GROUP_UVS')
        col.prop(self, 'samples', text='')
        col.prop(self, 'margin', text='')

        if height_root_ch:
            col.prop(self, 'use_baked_disp')

    def execute(self, context):
        mat = get_active_material()
        node = get_active_ypaint_node()
        yp = node.node_tree.yp
        tree = node.node_tree
        ypui = context.window_manager.ypui
        active_layer = None
        if len(yp.layers) > 0:
            active_layer = yp.layers[yp.active_layer_index]

        if self.target_type == 'MASK' and not active_layer:
            self.report({'ERROR'}, "Mask need active layer!")
            return {'CANCELLED'}

        if self.overwrite and self.overwrite_name == '':
            self.report({'ERROR'}, "Overwrite layer/mask cannot be empty!")
            return {'CANCELLED'}

        # Prepare bake settings
        remember_before_bake(self, context, yp)
        prepare_bake_settings(self, context, yp)

        # If use baked disp, need to bake normal and height map first
        height_root_ch = get_root_height_channel(yp)
        if height_root_ch and self.use_baked_disp:

            # Check if baked displacement already there
            baked_disp = tree.nodes.get(height_root_ch.baked_disp)

            if baked_disp and baked_disp.image:
                disp_width = baked_disp.image.size[0]
                disp_height = baked_disp.image.size[1]
            else:
                disp_width = 1024
                disp_height = 1024

            if yp.baked_uv_name != '':
                disp_uv = yp.baked_uv_name
            else: disp_uv = yp.uvs[0].name
            
            # Use 1 sample for baking height
            context.scene.cycles.samples = 1

            # Bake height channel
            bake_channel(disp_width, disp_height, disp_uv, mat, node, height_root_ch)

            # Set baked name
            if yp.baked_uv_name == '':
                yp.baked_uv_name = disp_uv

            # Recover original bake samples
            context.scene.cycles.samples = self.samples

            # Get subsurf modifier
            #subsurf = get_subsurf_modifier(obj)

            #subsurf_found = True
            #if not subsurf:
            #    subsurf_found = False
            #    subsurf = obj.modifiers.new('_temp_subsurf', 'SUBSURF')
            #    subsurf.render_levels = height_ch.subdiv_on_level
            #else:
            #    ori_level = subsurf.render_levels

            ## Get displace modifier
            #displace = get_displace_modifier(obj)
            #if not displace:
            #    displace = obj.modifiers.new('_temp_displace', 'DISPLACE')

            #end_max_height = tree.nodes.get(height_root_ch.end_max_height)
            #max_height = end_max_height.outputs[0].default_value

            #displace.strength = height_root_ch.subdiv_tweak * max_height
            #displace.mid_level = height_root_ch.parallax_ref_plane
            #displace.uv_layer = disp_uv

            # Set to use baked
            yp.use_baked = True
            ori_subdiv_setup = height_root_ch.enable_subdiv_setup
            ori_subdiv_adaptive = height_root_ch.subdiv_adaptive
            height_root_ch.subdiv_adaptive = False
            height_root_ch.enable_subdiv_setup = True

        #return {'FINISHED'}

        # Create bake nodes
        tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
        emit = mat.node_tree.nodes.new('ShaderNodeEmission')

        # Get output node and remember original bsdf input
        output = get_active_mat_output_node(mat.node_tree)
        ori_bsdf = output.inputs[0].links[0].from_socket

        if self.type == 'AO':
            src = mat.node_tree.nodes.new('ShaderNodeAmbientOcclusion')
            src.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)

            # Links
            if bpy.app.version_string.startswith('2.8'):
                src.inputs[1].default_value = self.ao_distance

                mat.node_tree.links.new(src.outputs[0], emit.inputs[0])
                mat.node_tree.links.new(emit.outputs[0], output.inputs[0])
            else:

                if context.scene.world:
                    context.scene.world.light_settings.distance = self.ao_distance

                mat.node_tree.links.new(src.outputs[0], output.inputs[0])

        elif self.type == 'POINTINESS':
            src = mat.node_tree.nodes.new('ShaderNodeNewGeometry')

            # Links
            mat.node_tree.links.new(src.outputs['Pointiness'], emit.inputs[0])
            mat.node_tree.links.new(emit.outputs[0], output.inputs[0])

        # New target image
        image = bpy.data.images.new(name=self.name,
                width=self.width, height=self.height, alpha=True, float_buffer=self.hdr)
        image.generated_color = (1.0, 1.0, 1.0, 1.0) if self.type == 'AO' else (0.73, 0.73, 0.73, 1.0)
        image.colorspace_settings.name = 'Linear'

        # Set bake image
        tex.image = image
        mat.node_tree.nodes.active = tex

        # Bake!
        bpy.ops.object.bake()

        overwrite_img = None
        if self.overwrite:
            overwrite_img = bpy.data.images.get(self.overwrite_name)

        if overwrite_img:
            replaced_layer_ids = replace_image(overwrite_img, image, yp, self.uv_map)
            if replaced_layer_ids and yp.active_layer_index not in replaced_layer_ids:
                active_id = replaced_layer_ids[0]
            else: active_id = yp.active_layer_index

            if self.target_type == 'MASK':
                # Activate mask
                for mask in yp.layers[yp.active_layer_index].masks:
                    if mask.type == 'IMAGE':
                        source = get_mask_source(mask)
                        if source.image and source.image == image:
                            mask.active_edit = True

        elif self.target_type == 'LAYER':
            yp.halt_update = True
            layer = Layer.add_new_layer(node.node_tree, image.name, 'IMAGE', int(self.channel_idx), self.blend_type, 
                    self.normal_blend_type, self.normal_map_type, 'UV', self.uv_map, image)
            yp.halt_update = False
            active_id = yp.active_layer_index
        else:
            mask = Mask.add_new_mask(active_layer, image.name, 'IMAGE', 'UV', self.uv_map, image)
            mask.active_edit = True

            rearrange_layer_nodes(active_layer)
            reconnect_layer_nodes(active_layer)

            active_id = yp.active_layer_index

        # Remove temp bake nodes
        simple_remove_node(mat.node_tree, tex)
        #simple_remove_node(mat.node_tree, srgb2lin)
        simple_remove_node(mat.node_tree, emit)
        simple_remove_node(mat.node_tree, src)

        # Recover original bsdf
        mat.node_tree.links.new(ori_bsdf, output.inputs[0])

        # Recover subdiv setup
        if height_root_ch and self.use_baked_disp:
            yp.use_baked = False
            height_root_ch.subdiv_adaptive = ori_subdiv_adaptive
            height_root_ch.enable_subdiv_setup = ori_subdiv_setup

        # Recover bake settings
        recover_bake_settings(self, context, yp)

        #return {'FINISHED'}

        # Reconnect and rearrange nodes
        #reconnect_yp_layer_nodes(node.node_tree)
        reconnect_yp_nodes(node.node_tree)
        rearrange_yp_nodes(node.node_tree)

        # Refresh active index
        if active_id != yp.active_layer_index:
            yp.active_layer_index = active_id

        if self.target_type == 'MASK':
            ypui.layer_ui.expand_masks = True
        ypui.need_update = True

        # Refresh mapping and stuff
        #yp.active_layer_index = yp.active_layer_index

        return {'FINISHED'}

class YTransferSomeLayerUV(bpy.types.Operator):
    bl_idname = "node.y_transfer_some_layer_uv"
    bl_label = "Transfer Some Layer UV"
    bl_description = "Transfer some layers/masks UV by baking it to other uv (this will take quite some time to finish)."
    bl_options = {'REGISTER', 'UNDO'}

    from_uv_map = StringProperty(default='')
    uv_map = StringProperty(default='')
    uv_map_coll = CollectionProperty(type=bpy.types.PropertyGroup)

    samples = IntProperty(name='Bake Samples', 
            description='Bake Samples, more means less jagged on generated textures', 
            default=1, min=1)

    margin = IntProperty(name='Bake Margin',
            description = 'Bake margin in pixels',
            default=5, subtype='PIXEL')

    @classmethod
    def poll(cls, context):
        return get_active_ypaint_node() and context.object.type == 'MESH' # and hasattr(context, 'layer')

    def invoke(self, context, event):
        obj = self.obj = context.object
        scene = self.scene = context.scene

        # Use active uv layer name by default
        if hasattr(obj.data, 'uv_textures'):
            uv_layers = self.uv_layers = obj.data.uv_textures
        else: uv_layers = self.uv_layers = obj.data.uv_layers

        # UV Map collections update
        self.uv_map_coll.clear()
        for uv in uv_layers:
            if not uv.name.startswith(TEMP_UV):
                self.uv_map_coll.add().name = uv.name

        return context.window_manager.invoke_props_dialog(self, width=320)

    def check(self, context):
        return True

    def draw(self, context):

        if bpy.app.version_string.startswith('2.8'):
            row = self.layout.split(factor=0.4)
        else: row = self.layout.split(percentage=0.4)

        col = row.column(align=False)
        col.label(text='From UV:')
        col.label(text='To UV:')
        col.label(text='Samples:')
        col.label(text='Margin:')

        col = row.column(align=False)
        col.prop_search(self, "from_uv_map", self, "uv_map_coll", text='', icon='GROUP_UVS')
        col.prop_search(self, "uv_map", self, "uv_map_coll", text='', icon='GROUP_UVS')
        col.prop(self, 'samples', text='')
        col.prop(self, 'margin', text='')

    def execute(self, context):

        T = time.time()

        if self.from_uv_map == '' or self.uv_map == '':
            self.report({'ERROR'}, "From or To UV Map cannot be empty!")
            return {'CANCELLED'}

        if self.from_uv_map == self.uv_map:
            self.report({'ERROR'}, "From and To UV cannot have same value!")
            return {'CANCELLED'}

        mat = get_active_material()
        node = get_active_ypaint_node()
        yp = node.node_tree.yp

        # Prepare bake settings
        remember_before_bake(self, context, yp)
        prepare_bake_settings(self, context, yp)

        for layer in yp.layers:
            #print(layer.name)
            if layer.type == 'IMAGE' and layer.uv_name == self.from_uv_map:
                transfer_uv(self.obj, mat, layer, self.uv_map)

            for mask in layer.masks:
                if mask.type == 'IMAGE' and mask.uv_name == self.from_uv_map:
                    transfer_uv(self.obj, mat, mask, self.uv_map)

        #return {'FINISHED'}

        # Recover bake settings
        recover_bake_settings(self, context, yp)

        # Refresh mapping and stuff
        yp.active_layer_index = yp.active_layer_index

        print('INFO: All layer and masks that using', self.from_uv_map, 'is transferred to', self.uv_map, 'at', '{:0.2f}'.format(time.time() - T), 'seconds!')

        return {'FINISHED'}

class YTransferLayerUV(bpy.types.Operator):
    bl_idname = "node.y_transfer_layer_uv"
    bl_label = "Transfer Layer UV"
    bl_description = "Transfer Layer UV by baking it to other uv (this will take quite some time to finish)."
    bl_options = {'REGISTER', 'UNDO'}

    uv_map = StringProperty(default='')
    uv_map_coll = CollectionProperty(type=bpy.types.PropertyGroup)

    samples = IntProperty(name='Bake Samples', 
            description='Bake Samples, more means less jagged on generated textures', 
            default=1, min=1)

    margin = IntProperty(name='Bake Margin',
            description = 'Bake margin in pixels',
            default=5, subtype='PIXEL')

    @classmethod
    def poll(cls, context):
        return get_active_ypaint_node() and context.object.type == 'MESH' # and hasattr(context, 'layer')

    def invoke(self, context, event):
        obj = self.obj = context.object
        scene = self.scene = context.scene

        if hasattr(context, 'mask'):
            self.entity = context.mask

        elif hasattr(context, 'layer'):
            self.entity = context.layer

        if not self.entity:
            return self.execute(context)

        # Use active uv layer name by default
        if hasattr(obj.data, 'uv_textures'):
            uv_layers = self.uv_layers = obj.data.uv_textures
        else: uv_layers = self.uv_layers = obj.data.uv_layers

        # UV Map collections update
        self.uv_map_coll.clear()
        for uv in uv_layers:
            if not uv.name.startswith(TEMP_UV) and uv.name != self.entity.uv_name:
                self.uv_map_coll.add().name = uv.name

        return context.window_manager.invoke_props_dialog(self, width=320)

    def check(self, context):
        return True

    def draw(self, context):
        if bpy.app.version_string.startswith('2.8'):
            row = self.layout.split(factor=0.4)
        else: row = self.layout.split(percentage=0.4)

        col = row.column(align=False)
        col.label(text='Target UV:')
        col.label(text='Samples:')
        col.label(text='Margin:')

        col = row.column(align=False)
        col.prop_search(self, "uv_map", self, "uv_map_coll", text='', icon='GROUP_UVS')
        col.prop(self, 'samples', text='')
        col.prop(self, 'margin', text='')

    def execute(self, context):
        T = time.time()

        if not hasattr(self, 'entity'):
            return {'CANCELLED'}

        if self.entity.type != 'IMAGE' or self.entity.texcoord_type != 'UV':
            self.report({'ERROR'}, "Only works with image layer/mask with UV Mapping")
            return {'CANCELLED'}

        if self.uv_map == '':
            self.report({'ERROR'}, "Target UV Map cannot be empty!")
            return {'CANCELLED'}

        if self.uv_map == self.entity.uv_name:
            self.report({'ERROR'}, "This layer/mask already use " + self.uv_map + "!")
            return {'CANCELLED'}

        mat = get_active_material()
        yp = self.entity.id_data.yp

        # Prepare bake settings
        remember_before_bake(self, context, yp)
        prepare_bake_settings(self, context, yp)

        # Transfer UV
        transfer_uv(self.obj, mat, self.entity, self.uv_map)

        # Recover bake settings
        recover_bake_settings(self, context, yp)

        # Refresh mapping and stuff
        yp.active_layer_index = yp.active_layer_index

        print('INFO:', self.entity.name, 'UV is transferred from', self.entity.uv_name, 'to', self.uv_map, 'at', '{:0.2f}'.format(time.time() - T), 'seconds!')

        return {'FINISHED'}

def remember_before_resize(self, context):
    scene = context.scene
    self.object = context.object
    self.mode = context.object.mode
    self.selected_objects = [ob for ob in context.selected_objects]

    # Remember render settings
    self.ori_engine = scene.render.engine
    self.ori_bake_type = scene.cycles.bake_type
    self.ori_samples = scene.cycles.samples
    self.ori_threads_mode = scene.render.threads_mode
    self.ori_margin = scene.render.bake.margin
    self.ori_use_clear = scene.render.bake.use_clear

def prepare_bake_resize_settings(self, context):
    scene = context.scene

    scene.render.engine = 'CYCLES'
    scene.cycles.bake_type = 'EMIT'
    scene.cycles.samples = 1
    scene.render.threads_mode = 'AUTO'
    scene.render.bake.margin = 0
    scene.render.bake.use_clear = False

def recover_after_resize(self, context):
    scene = context.scene

    scene.render.engine = self.ori_engine
    scene.cycles.bake_type = self.ori_bake_type
    scene.cycles.samples = self.ori_samples
    scene.render.threads_mode = self.ori_threads_mode
    scene.render.bake.margin = self.ori_margin
    scene.render.bake.use_clear = self.ori_use_clear

    # Deselect all
    if bpy.app.version_string.startswith('2.8'):
        for obj in context.view_layer.objects:
            obj.select_set(False)
    else:
        for obj in scene.objects:
            obj.select = False

    # Recover select
    if bpy.app.version_string.startswith('2.8'):
        for obj in self.selected_objects:
            obj.select_set(True)
    else:
        for obj in self.selected_objects:
            obj.select = True

    if bpy.app.version_string.startswith('2.8'):
        context.view_layer.objects.active = self.object
    else: scene.objects.active = self.object

    bpy.ops.object.mode_set(mode = self.mode)

class YResizeImage(bpy.types.Operator):
    bl_idname = "node.y_resize_image"
    bl_label = "Resize Image Layer/Mask"
    bl_description = "Resize image of layer or mask"
    bl_options = {'REGISTER', 'UNDO'}

    width = IntProperty(name='Width', default = 1024, min=1, max=4096)
    height = IntProperty(name='Height', default = 1024, min=1, max=4096)

    @classmethod
    def poll(cls, context):
        #return hasattr(context, 'image') and hasattr(context, 'layer')
        return get_active_ypaint_node() and context.object.type == 'MESH'

    def invoke(self, context, event):
        if hasattr(context, 'image') and hasattr(context, 'layer'):
            self.image = context.image
            self.layer = context.layer
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        if bpy.app.version_string.startswith('2.8'):
            row = self.layout.split(factor=0.4)
        else: row = self.layout.split(percentage=0.4)
        col = row.column(align=True)

        col.label(text='Width:')
        col.label(text='Height:')

        col = row.column(align=True)

        col.prop(self, 'width', text='')
        col.prop(self, 'height', text='')

    def execute(self, context):
        if not hasattr(self, 'image') or not hasattr(self, 'layer'):
            self.report({'ERROR'}, "No active image/layer found!")
            return {'CANCELLED'}

        image = self.image
        layer = self.layer
        yp = layer.id_data.yp

        remember_before_resize(self, context)
        prepare_bake_resize_settings(self, context)

        # Get entity
        entity = layer
        mask_entity = False
        for mask in layer.masks:
            if mask.active_edit:
                entity = mask
                mask_entity = True
                break

        # Get original size
        segment = None
        if image.yia.is_image_atlas:
            segment = image.yia.segments.get(entity.segment_name)
            ori_width = segment.width
            ori_height = segment.height
        else:
            ori_width = image.size[0]
            ori_height = image.size[1]

        if ori_width == self.width and ori_height == self.height:
            self.report({'ERROR'}, "This image already had the same size!")
            return {'CANCELLED'}

        if segment:
            new_segment = ImageAtlas.get_set_image_atlas_segment(
                        self.width, self.height, image.yia.color, image.is_float) #, ypup.image_atlas_size)
            scaled_img = new_segment.id_data

            ori_start_x = segment.width * segment.tile_x
            ori_start_y = segment.height * segment.tile_y

            start_x = self.width * new_segment.tile_x
            start_y = self.height * new_segment.tile_y
        else:
            scaled_img = bpy.data.images.new(name='__TEMP__', 
                width=self.width, height=self.height, alpha=True, float_buffer=image.is_float)
            scaled_img.colorspace_settings.name = 'Linear'
            if image.filepath != '' and not image.packed_file:
                scaled_img.filepath = image.filepath

            start_x = 0
            start_y = 0

        # Deselect all objects
        if bpy.app.version_string.startswith('2.8'):
            for obj in self.selected_objects:
                obj.select_set(False)
        else:
            for obj in self.selected_objects:
                obj.select = False

        # Create new plane
        bpy.ops.object.mode_set(mode = 'OBJECT')
        bpy.ops.mesh.primitive_plane_add(calc_uvs=True)
        plane_obj = context.view_layer.objects.active

        # If using image atlas, transform uv
        if segment:
            if bpy.app.version_string.startswith('2.8'):
                uv_layers = plane_obj.data.uv_layers
            else: uv_layers = plane_obj.data.uv_textures

            # Transform current uv using previous segment
            #uv_layer = uv_layers.active
            for i, d in enumerate(plane_obj.data.uv_layers.active.data):
                if i == 0: # Top right
                    d.uv.x = (ori_start_x + segment.width) / image.size[0]
                    d.uv.y = (ori_start_y + segment.height) / image.size[1]
                elif i == 1: # Top left
                    d.uv.x = ori_start_x / image.size[0]
                    d.uv.y = (ori_start_y + segment.height) / image.size[1]
                elif i == 2: # Bottom left
                    d.uv.x = ori_start_x / image.size[0]
                    d.uv.y = ori_start_y / image.size[1]
                elif i == 3: # Bottom right
                    d.uv.x = (ori_start_x + segment.width) / image.size[0]
                    d.uv.y = ori_start_y / image.size[1]

            # Create new uv and transform it using new segment
            temp_uv_layer = uv_layers.new(name='__TEMP')
            uv_layers.active = temp_uv_layer
            for i, d in enumerate(plane_obj.data.uv_layers.active.data):
                if i == 0: # Top right
                    d.uv.x = (start_x + self.width) / scaled_img.size[0]
                    d.uv.y = (start_y + self.height) / scaled_img.size[1]
                elif i == 1: # Top left
                    d.uv.x = start_x / scaled_img.size[0]
                    d.uv.y = (start_y + self.height) / scaled_img.size[1]
                elif i == 2: # Bottom left
                    d.uv.x = start_x / scaled_img.size[0]
                    d.uv.y = start_y / scaled_img.size[1]
                elif i == 3: # Bottom right
                    d.uv.x = (start_x + self.width) / scaled_img.size[0]
                    d.uv.y = start_y / scaled_img.size[1]

        #return{'FINISHED'}

        mat = bpy.data.materials.new('__TEMP__')
        mat.use_nodes = True
        plane_obj.active_material = mat

        output = get_active_mat_output_node(mat.node_tree)
        emi = mat.node_tree.nodes.new('ShaderNodeEmission')
        uv_map = mat.node_tree.nodes.new('ShaderNodeUVMap')
        uv_map.uv_map = 'UVMap'
        target_tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
        target_tex.image = scaled_img
        source_tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
        source_tex.image = image
        straight_over = mat.node_tree.nodes.new('ShaderNodeGroup')
        straight_over.node_tree = get_node_tree_lib(lib.STRAIGHT_OVER)
        straight_over.inputs[1].default_value = 0.0

        # Connect nodes
        mat.node_tree.links.new(uv_map.outputs[0], source_tex.inputs[0])
        mat.node_tree.links.new(source_tex.outputs[0], straight_over.inputs[2])
        mat.node_tree.links.new(source_tex.outputs[1], straight_over.inputs[3])
        mat.node_tree.links.new(straight_over.outputs[0], emi.inputs[0])
        mat.node_tree.links.new(emi.outputs[0], output.inputs[0])
        mat.node_tree.nodes.active = target_tex

        # Bake
        bpy.ops.object.bake()

        # Create alpha image as bake target
        alpha_img = bpy.data.images.new(name='__TEMP_ALPHA__',
                width=self.width, height=self.height, alpha=True, float_buffer=image.is_float)
        alpha_img.colorspace_settings.name = 'Linear'

        # Retransform back uv
        if segment:
            for i, d in enumerate(plane_obj.data.uv_layers.active.data):
                if i == 0: # Top right
                    d.uv.x = 1.0
                    d.uv.y = 1.0
                elif i == 1: # Top left
                    d.uv.x = 0.0
                    d.uv.y = 1.0
                elif i == 2: # Bottom left
                    d.uv.x = 0.0
                    d.uv.y = 0.0
                elif i == 3: # Bottom right
                    d.uv.x = 1.0
                    d.uv.y = 0.0

        # Setup texture
        target_tex.image = alpha_img
        mat.node_tree.links.new(source_tex.outputs[1], emi.inputs[0])

        # Bake again!
        bpy.ops.object.bake()

        # Copy alpha image to scaled image
        target_pxs = list(scaled_img.pixels)
        temp_pxs = list(alpha_img.pixels)

        for y in range(self.height):
            temp_offset_y = self.width * 4 * y
            offset_y = scaled_img.size[0] * 4 * (y + start_y)
            for x in range(self.width):
                temp_offset_x = 4 * x
                offset_x = 4 * (x + start_x)
                target_pxs[offset_y + offset_x + 3] = temp_pxs[temp_offset_y + temp_offset_x]

        scaled_img.pixels = target_pxs

        # Replace original image to scaled image
        replace_image(image, scaled_img)

        # Remove temp datas
        if straight_over.node_tree.users == 1:
            bpy.data.node_groups.remove(straight_over.node_tree)
        bpy.data.images.remove(alpha_img)
        bpy.data.materials.remove(mat)
        plane = plane_obj.data
        bpy.ops.object.delete()
        bpy.data.meshes.remove(plane)

        # Recover settings
        recover_after_resize(self, context)

        if segment:
            entity.segment_name = new_segment.name
            segment.unused = True
            update_mapping(entity)

        # Refresh active layer
        yp.active_layer_index = yp.active_layer_index

        return {'FINISHED'}

class YBakeChannels(bpy.types.Operator):
    """Bake Channels to Image(s)"""
    bl_idname = "node.y_bake_channels"
    bl_label = "Bake channels to Image"
    bl_options = {'REGISTER', 'UNDO'}

    width = IntProperty(name='Width', default = 1024, min=1, max=4096)
    height = IntProperty(name='Height', default = 1024, min=1, max=4096)

    uv_map = StringProperty(default='')
    uv_map_coll = CollectionProperty(type=bpy.types.PropertyGroup)

    samples = IntProperty(name='Bake Samples', 
            description='Bake Samples, more means less jagged on generated textures', 
            default=1, min=1)

    margin = IntProperty(name='Bake Margin',
            description = 'Bake margin in pixels',
            default=5, subtype='PIXEL')

    @classmethod
    def poll(cls, context):
        return get_active_ypaint_node() and context.object.type == 'MESH'

    def invoke(self, context, event):
        node = get_active_ypaint_node()
        yp = node.node_tree.yp
        obj = self.obj = context.object
        scene = self.scene = context.scene

        # Use active uv layer name by default
        if hasattr(obj.data, 'uv_textures'):
            uv_layers = self.uv_layers = obj.data.uv_textures
        else: uv_layers = self.uv_layers = obj.data.uv_layers

        # Use active uv layer name by default
        if obj.type == 'MESH' and len(uv_layers) > 0:
            active_name = uv_layers.active.name
            if active_name == TEMP_UV:
                self.uv_map = yp.layers[yp.active_layer_index].uv_name
            else: self.uv_map = uv_layers.active.name

            # UV Map collections update
            self.uv_map_coll.clear()
            for uv in uv_layers:
                if not uv.name.startswith(TEMP_UV):
                    self.uv_map_coll.add().name = uv.name

        return context.window_manager.invoke_props_dialog(self, width=320)

    def check(self, context):
        return True

    def draw(self, context):
        if bpy.app.version_string.startswith('2.8'):
            row = self.layout.split(factor=0.4)
        else: row = self.layout.split(percentage=0.4)
        col = row.column(align=True)

        col.label(text='Width:')
        col.label(text='Height:')
        col.separator()
        col.label(text='Samples:')
        col.label(text='Margin:')
        col.separator()
        col.label(text='UV Map:')

        col = row.column(align=True)

        col.prop(self, 'width', text='')
        col.prop(self, 'height', text='')
        col.separator()

        col.prop(self, 'samples', text='')
        col.prop(self, 'margin', text='')
        col.separator()

        col.prop_search(self, "uv_map", self, "uv_map_coll", text='', icon='GROUP_UVS')

    def execute(self, context):

        T = time.time()

        mat = get_active_material()
        node = get_active_ypaint_node()
        tree = node.node_tree
        yp = tree.yp
        ypui = context.window_manager.ypui
        obj = context.object

        remember_before_bake(self, context, yp)

        if BL28_HACK:
        #if bpy.app.version_string.startswith('2.8'):

            if len(yp.uvs) > MAX_VERTEX_DATA - len(obj.data.vertex_colors):
                self.report({'ERROR'}, "Maximum vertex colors reached! Need at least " + str(len(uvs)) + " vertex color(s)!")
                return {'CANCELLED'}

            # Update tangent sign vertex color
            for uv in yp.uvs:
                tangent_process = tree.nodes.get(uv.tangent_process)
                if tangent_process:
                    tangent_process.inputs['Backface Always Up'].default_value = 1.0 if yp.enable_backface_always_up else 0.0
                    tangent_process.inputs['Blender 2.8 Cycles Hack'].default_value = 1.0
                    tansign = tangent_process.node_tree.nodes.get('_tangent_sign')
                    vcol = refresh_tangent_sign_vcol(obj, uv.name)
                    if vcol: tansign.attribute_name = vcol.name

        # Disable use baked first
        if yp.use_baked:
            yp.use_baked = False

        # Prepare bake settings
        prepare_bake_settings(self, context, yp)

        # Bake channels
        for ch in yp.channels:
            bake_channel(self.width, self.height, self.uv_map, mat, node, ch)

        # Set baked uv
        yp.baked_uv_name = self.uv_map

        # Recover bake settings
        recover_bake_settings(self, context, yp)

        # Use bake results
        yp.halt_update = True
        yp.use_baked = True
        yp.halt_update = False

        # Check subdiv Setup
        height_ch = get_root_height_channel(yp)
        check_subdiv_setup(height_ch)

        # Update global uv
        check_uv_nodes(yp)

        # Recover hack
        #if bpy.app.version_string.startswith('2.8'):
        if BL28_HACK:
            # Refresh tangent sign hacks
            update_enable_tangent_sign_hacks(yp, context)

        # Rearrange
        rearrange_yp_nodes(tree)
        reconnect_yp_nodes(tree)

        print('INFO:', tree.name, 'channels is baked at', '{:0.2f}'.format(time.time() - T), 'seconds!')

        return {'FINISHED'}

#class YDisableBakeResult(bpy.types.Operator):
#    """Disable Baked Image Result"""
#    bl_idname = "node.y_disable_baked_result"
#    bl_label = "Disable Bake Result"
#    bl_options = {'REGISTER', 'UNDO'}
#
#    @classmethod
#    def poll(cls, context):
#        node = get_active_ypaint_node()
#        return node and node.node_tree.yp.use_baked
#
#    def execute(self, context):
#        node = get_active_ypaint_node()
#        tree = node.node_tree
#        yp = tree.yp
#
#        yp.use_baked = False
#
#        reconnect_yp_nodes(tree)
#
#        return {'FINISHED'}

def update_use_baked(self, context):
    tree = self.id_data
    yp = tree.yp

    if yp.halt_update: return

    # Check subdiv setup
    height_ch = get_root_height_channel(yp)
    check_subdiv_setup(height_ch)

    # Check uv nodes
    check_uv_nodes(yp)

    # Reconnect nodes
    rearrange_yp_nodes(tree)
    reconnect_yp_nodes(tree)

    # Trigger active image update
    if self.use_baked:
        self.active_channel_index = self.active_channel_index
    else:
        self.active_layer_index = self.active_layer_index

def check_subdiv_setup(height_ch):
    tree = height_ch.id_data
    yp = tree.yp

    if not height_ch: return
    obj = bpy.context.object
    mat = obj.active_material
    scene = bpy.context.scene

    # Get height image and max height
    baked_disp = tree.nodes.get(height_ch.baked_disp)
    end_max_height = tree.nodes.get(height_ch.end_max_height)
    if not baked_disp or not baked_disp.image or not end_max_height: return
    img = baked_disp.image
    max_height = end_max_height.outputs[0].default_value

    # Max height tweak node
    if yp.use_baked and height_ch.enable_subdiv_setup:
        end_max_height = check_new_node(tree, height_ch, 'end_max_height_tweak', 'ShaderNodeMath', 'Max Height Tweak')
        end_max_height.operation = 'MULTIPLY'
        end_max_height.inputs[1].default_value = height_ch.subdiv_tweak
    else:
        remove_node(tree, height_ch, 'end_max_height_tweak')

    # Subsurf
    subsurf = get_subsurf_modifier(obj)

    if yp.use_baked and height_ch.enable_subdiv_setup and not height_ch.subdiv_adaptive:

        if not subsurf:
            subsurf = obj.modifiers.new('Subsurf', 'SUBSURF')

        subsurf.render_levels = height_ch.subdiv_on_level
        subsurf.levels = height_ch.subdiv_on_level

    elif subsurf:
        subsurf.render_levels = height_ch.subdiv_off_level
        subsurf.levels = height_ch.subdiv_off_level

    # Set subsurf to visible
    if subsurf:
        subsurf.show_render = True
        subsurf.show_viewport = True

    # Displace
    if yp.use_baked and height_ch.enable_subdiv_setup and not height_ch.subdiv_adaptive:

        displace = get_displace_modifier(obj)
        if not displace:
            displace = obj.modifiers.new('yP_Displace', 'DISPLACE')

        tex = displace.texture
        if not tex:
            tex = bpy.data.textures.new(img.name, 'IMAGE')
            tex.image = img
            displace.texture = tex
            displace.texture_coords = 'UV'

        displace.strength = height_ch.subdiv_tweak * max_height
        displace.mid_level = height_ch.parallax_ref_plane
        displace.uv_layer = yp.baked_uv_name

        # Set displace to visible
        displace.show_render = True
        displace.show_viewport = True

    else:

        for mod in obj.modifiers:
            if mod.type == 'DISPLACE' and mod.name == 'yP_Displace':
                if mod.texture:
                    bpy.data.textures.remove(mod.texture)
                obj.modifiers.remove(mod)

    # Get active output material
    try: output_mat = [n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output][0]
    except: return

    # Get active ypaint node
    node = get_active_ypaint_node()
    norm_outp = node.outputs[height_ch.name]

    # Recover normal for Blender 2.7
    if not bpy.app.version_string.startswith('2.8'):

        if not yp.use_baked or not height_ch.enable_subdiv_setup or (
                height_ch.enable_subdiv_setup and not height_ch.subdiv_adaptive):

            # Relink will only be proceed if no new links found
            link_found = any([l for l in norm_outp.links])
            if not link_found:

                # Try to relink to original connections
                for con in height_ch.ori_normal_to:
                    try:
                        node_to = mat.node_tree.nodes.get(con.node)
                        socket_to = node_to.inputs[con.socket]
                        if len(socket_to.links) < 1:
                            mat.node_tree.links.new(norm_outp, socket_to)
                    except: pass
                
            height_ch.ori_normal_to.clear()

    # Adaptive subdiv
    if yp.use_baked and height_ch.enable_subdiv_setup and height_ch.subdiv_adaptive:

        if not subsurf:
            subsurf = obj.modifiers.new('Subsurf', 'SUBSURF')
            subsurf.render_levels = height_ch.subdiv_off_level
            subsurf.levels = height_ch.subdiv_off_level

        # Adaptive subdivision only works for experimental feature set for now
        scene.cycles.feature_set = 'EXPERIMENTAL'
        scene.cycles.dicing_rate = height_ch.subdiv_global_dicing
        scene.cycles.preview_dicing_rate = height_ch.subdiv_global_dicing
        obj.cycles.use_adaptive_subdivision = True

        # Check output connection
        height_outp = node.outputs[height_ch.name + io_suffix['HEIGHT']]
        max_height_outp = node.outputs[height_ch.name + io_suffix['MAX_HEIGHT']]

        if bpy.app.version_string.startswith('2.8'):
            #mat.cycles.displacement_method = 'BOTH'
            mat.cycles.displacement_method = 'DISPLACEMENT'

            # Search for displacement node
            height_matches = []
            for link in height_outp.links:
                if link.to_node.type == 'DISPLACEMENT':
                    #disp = link.to_node
                    height_matches.append(link.to_node)

            max_height_matches = []
            for link in max_height_outp.links:
                if link.to_node.type == 'DISPLACEMENT':
                    max_height_matches.append(link.to_node)

            disp = None
            for n in height_matches:
                if n in max_height_matches:
                    disp = n
                    break

            # Create one if there's none
            if not disp:
                disp = mat.node_tree.nodes.new('ShaderNodeDisplacement')
                disp.location.x = node.location.x #+ 200
                disp.location.y = node.location.y - 400

            create_link(mat.node_tree, disp.outputs[0], output_mat.inputs['Displacement'])
            create_link(mat.node_tree, height_outp, disp.inputs['Height'])
            create_link(mat.node_tree, max_height_outp, disp.inputs['Scale'])

        else:
            # Remember normal connection, because it will be disconnected to avoid render error
            for link in norm_outp.links:
                con = height_ch.ori_normal_to.add()
                con.node = link.to_node.name
                con.socket = link.to_socket.name

            # Remove normal connection because it will produce render error
            break_output_link(mat.node_tree, norm_outp)

            # Set displacement mode
            #mat.cycles.displacement_method = 'BOTH'
            mat.cycles.displacement_method = 'TRUE'

            # Search for displacement node
            height_matches = []
            for link in height_outp.links:
                #if link.to_node.type == 'MATH' and link.to_node.operation == 'MULTIPLY':
                if link.to_node.type == 'GROUP' and link.to_node.node_tree.name == lib.BL27_DISP:
                    #disp = link.to_node
                    height_matches.append(link.to_node)

            max_height_matches = []
            for link in max_height_outp.links:
                #if link.to_node.type == 'MATH' and link.to_node.operation == 'MULTIPLY':
                if link.to_node.type == 'GROUP' and link.to_node.node_tree.name == lib.BL27_DISP:
                    max_height_matches.append(link.to_node)

            disp = None
            for n in height_matches:
                if n in max_height_matches:
                    disp = n
                    break

            # Create one if there's none
            if not disp:
                #disp = mat.node_tree.nodes.new('ShaderNodeMath')
                #disp.operation = 'MULTIPLY'
                disp = mat.node_tree.nodes.new('ShaderNodeGroup')
                disp.node_tree = get_node_tree_lib(lib.BL27_DISP)
                disp.location.x = node.location.x #+ 200
                disp.location.y = node.location.y - 400

            create_link(mat.node_tree, disp.outputs[0], output_mat.inputs['Displacement'])
            create_link(mat.node_tree, height_outp, disp.inputs[0])
            create_link(mat.node_tree, max_height_outp, disp.inputs[1])

    else:
        obj.cycles.use_adaptive_subdivision = False

        # Back to supported feature set
        # NOTE: It's kinda forced, but whatever
        #scene.cycles.feature_set = 'SUPPORTED'

        # Remove displacement output material link
        # NOTE: It's very forced, but whatever
        break_input_link(mat.node_tree, output_mat.inputs['Displacement'])

def update_enable_subdiv_setup(self, context):
    obj = context.object
    tree = self.id_data
    yp = tree.yp

    # Check uv nodes to enable/disable parallax
    check_uv_nodes(yp)

    # Check subdiv setup
    check_subdiv_setup(self)

    # Reconnect nodes
    rearrange_yp_nodes(tree)
    reconnect_yp_nodes(tree)

def update_subdiv_tweak(self, context):
    obj = context.object
    tree = self.id_data
    yp = tree.yp

    height_ch = self

    displace = get_displace_modifier(obj)
    if displace:
        end_max_height = tree.nodes.get(height_ch.end_max_height)
        if end_max_height:
            displace.strength = height_ch.subdiv_tweak * end_max_height.outputs[0].default_value

    end_max_height_tweak = tree.nodes.get(height_ch.end_max_height_tweak)
    if end_max_height_tweak:
        end_max_height_tweak.inputs[1].default_value = height_ch.subdiv_tweak

def update_subdiv_on_off_level(self, context):
    obj = context.object
    tree = self.id_data
    yp = tree.yp

    height_ch = self

    subsurf = get_subsurf_modifier(obj)
    if not subsurf: return

    if self.enable_subdiv_setup and yp.use_baked and not self.subdiv_adaptive:
        subsurf.render_levels = height_ch.subdiv_on_level
        subsurf.levels = height_ch.subdiv_on_level
    else:
        subsurf.render_levels = height_ch.subdiv_off_level
        subsurf.levels = height_ch.subdiv_off_level

def update_subdiv_standard_type(self, context):
    obj = context.object
    tree = self.id_data
    yp = tree.yp

    height_ch = self

    subsurf = get_subsurf_modifier(obj)
    if not subsurf: return

    subsurf.subdivision_type = height_ch.subdiv_standard_type

def update_subdiv_global_dicing(self, context):
    scene = context.scene
    height_ch = self

    scene.cycles.dicing_rate = height_ch.subdiv_global_dicing
    scene.cycles.preview_dicing_rate = height_ch.subdiv_global_dicing

def register():
    bpy.utils.register_class(YBakeToLayer)
    bpy.utils.register_class(YTransferSomeLayerUV)
    bpy.utils.register_class(YTransferLayerUV)
    bpy.utils.register_class(YResizeImage)
    bpy.utils.register_class(YBakeChannels)
    #bpy.utils.register_class(YDisableBakeResult)

def unregister():
    bpy.utils.unregister_class(YBakeToLayer)
    bpy.utils.unregister_class(YTransferSomeLayerUV)
    bpy.utils.unregister_class(YTransferLayerUV)
    bpy.utils.unregister_class(YResizeImage)
    bpy.utils.unregister_class(YBakeChannels)
    #bpy.utils.unregister_class(YDisableBakeResult)
