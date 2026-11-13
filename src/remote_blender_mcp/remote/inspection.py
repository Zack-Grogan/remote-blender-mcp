"""Bounded, read-only queries for Blender agents; no evaluated expressions."""

import platform
from pathlib import Path

import bpy


def plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bpy.types.ID):
        return {"name": value.name, "type": value.bl_rna.identifier}
    try:
        return [plain(v) for v in value]
    except TypeError:
        return str(value)[:300]


def properties(rna, offset, limit, python_type=None, operator_properties=None):
    result = []
    props = [p for p in rna.properties if p.identifier != "rna_type"]
    for p in props[offset : offset + limit]:
        row = dict(
            identifier=p.identifier,
            type=p.type,
            description=p.description,
            readonly=p.is_readonly,
            array_length=getattr(p, "array_length", 0),
        )
        if p.type == "ENUM" and not p.enum_items:
            row["default"] = None
            row["default_note"] = "Static default unavailable for this dynamic enum"
        elif hasattr(p, "default"):
            row["default"] = plain(p.default_array if getattr(p, "is_array", False) else p.default)
        if p.type == "ENUM":
            items = [dict(identifier=e.identifier, name=e.name) for e in p.enum_items]
            row["items_source"] = "RNA"
            resolved = bool(items)
            if not items and python_type:
                # Blender registrations can keep dynamic enums on inherited mixins.
                for base in python_type.__mro__:
                    definition = base.__dict__.get("__annotations__", {}).get(p.identifier)
                    source = getattr(definition, "keywords", {}).get("items")
                    if source is None:
                        continue
                    try:
                        dynamic = callable(source)
                        raw = source(operator_properties, bpy.context) if dynamic else source
                        items = [dict(identifier=e[0], name=e[1]) for e in raw if e and e[0]]
                        row["items_source"] = "dynamic_callback" if dynamic else "python_registration"
                        row["context_dependent"] = dynamic
                        resolved = True
                    except Exception as exc:
                        row["items_error"] = str(exc)
                    break
            row["items"] = items[:100]
            row["total_items"] = len(items)
            row["items_complete"] = resolved and len(items) <= 100
            if not resolved:
                row["items_note"] = (
                    "Dynamic choices could not be resolved in this context; an empty list does not mean no choices exist"
                )
        if p.type in ("POINTER", "COLLECTION"):
            row["fixed_type"] = p.fixed_type.identifier
        result.append(row)
    return dict(identifier=rna.identifier, description=rna.description, total=len(props), properties=result)


def inspect(kind, params):
    offset, limit = params.get("offset", 0), params.get("limit", 100)
    if kind == "environment":
        result = dict(
            blender=bpy.app.version_string,
            python=platform.python_version(),
            background=bpy.app.background,
            workspace="/workspace",
            addons=sorted(bpy.context.preferences.addons.keys()),
            engines=sorted(
                {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
                | {getattr(t, "bl_idname", "") for t in bpy.types.RenderEngine.__subclasses__()} - {""}
            ),
            engines_complete=False,
            gpu_devices=[],
            graphics=None,
        )
        cycles = bpy.context.preferences.addons.get("cycles")
        if cycles:
            prefs = cycles.preferences
            prefs.get_devices()
            result["gpu_devices"] = [dict(name=d.name, type=d.type, enabled=d.use) for d in prefs.devices]
        if not bpy.app.background:
            import gpu

            result["graphics"] = dict(renderer=gpu.platform.renderer_get(), vendor=gpu.platform.vendor_get())
        result["capabilities"] = dict(
            python=True,
            viewport=not bpy.app.background,
            api_discovery=True,
            network=False,
            desktop_input=False,
        )
        return result
    if kind == "object":
        obj = bpy.data.objects.get(params["name"])
        if obj is None:
            raise ValueError(f"Unknown object: {params['name']}")
        result = dict(
            name=obj.name,
            type=obj.type,
            mode=obj.mode,
            parent=obj.parent.name if obj.parent else None,
            location=list(obj.location),
            rotation_euler=list(obj.rotation_euler),
            scale=list(obj.scale),
            dimensions=list(obj.dimensions),
            matrix_world=plain(obj.matrix_world),
            collections=[c.name for c in obj.users_collection],
            materials=[s.material.name if s.material else None for s in obj.material_slots],
            modifiers=[
                dict(
                    name=m.name, type=m.type, node_group=getattr(getattr(m, "node_group", None), "name", None)
                )
                for m in obj.modifiers
            ],
            constraints=[dict(name=c.name, type=c.type) for c in obj.constraints],
            animation_action=getattr(getattr(obj.animation_data, "action", None), "name", None),
            custom_properties={k: plain(v) for k, v in obj.items() if k != "_RNA_UI"},
        )
        if obj.type == "MESH":
            result["mesh"] = dict(
                vertices=len(obj.data.vertices),
                edges=len(obj.data.edges),
                polygons=len(obj.data.polygons),
                uv_layers=[uv.name for uv in obj.data.uv_layers],
            )
        if obj.type == "ARMATURE":
            result["bones"] = [
                dict(name=b.name, parent=b.parent.name if b.parent else None) for b in obj.data.bones
            ][:limit]
        return result
    if kind == "nodes":
        owner_type = params.get("owner_type", "material")
        collection = {
            "material": bpy.data.materials,
            "group": bpy.data.node_groups,
            "world": bpy.data.worlds,
            "scene": bpy.data.scenes,
        }[owner_type]
        owner = collection.get(params["name"])
        if owner is None:
            raise ValueError("Node owner not found")
        tree = owner if owner_type == "group" else getattr(owner, "node_tree", None)
        if tree is None:
            raise ValueError("Owner has no node tree; enable nodes first")
        selected = list(tree.nodes)[offset : offset + limit]
        names = {n.name for n in selected}
        return dict(
            name=tree.name,
            total_nodes=len(tree.nodes),
            nodes=[
                dict(
                    name=n.name,
                    type=n.bl_idname,
                    label=n.label,
                    location=list(n.location),
                    inputs=[
                        dict(
                            name=s.name,
                            identifier=s.identifier,
                            linked=s.is_linked,
                            default=plain(getattr(s, "default_value", None)),
                        )
                        for s in n.inputs
                    ],
                    outputs=[
                        dict(name=s.name, identifier=s.identifier, linked=s.is_linked) for s in n.outputs
                    ],
                )
                for n in selected
            ],
            links=[
                dict(
                    from_node=link.from_node.name,
                    from_socket=link.from_socket.name,
                    to_node=link.to_node.name,
                    to_socket=link.to_socket.name,
                )
                for link in tree.links
                if link.from_node.name in names or link.to_node.name in names
            ],
        )
    if kind == "dependencies":
        paths = []
        for collection in [
            bpy.data.images,
            bpy.data.libraries,
            bpy.data.fonts,
            bpy.data.sounds,
            bpy.data.movieclips,
            bpy.data.cache_files,
        ]:
            for item in collection:
                path = getattr(item, "filepath", "")
                if not path or path == "<builtin>":
                    continue
                absolute = bpy.path.abspath(path, library=getattr(item, "library", None))
                packed = bool(getattr(item, "packed_file", None) or getattr(item, "packed_files", None))
                template = any(c in path for c in ["<UDIM>", "####", "%"])
                paths.append(
                    dict(
                        name=item.name,
                        type=item.bl_rna.identifier,
                        path=path,
                        resolved=absolute,
                        packed=packed,
                        exists=None if template else Path(absolute).is_file(),
                    )
                )
        return dict(
            total=len(paths),
            dependencies=paths[offset : offset + limit],
            complete=False,
            note="Lists image/library/font/audio/movie/cache datablocks. Simulation caches, sequences and add-on dependencies need separate checks.",
        )
    if kind == "api":
        category, name = params.get("category", "operator"), params.get("name", "")
        if category == "operator":
            if not name:
                query = params.get("query", "").lower()
                names = sorted(
                    f"{group}.{op}"
                    for group in dir(bpy.ops)
                    if not group.startswith("_")
                    for op in dir(getattr(bpy.ops, group))
                    if not op.startswith("_") and query in f"{group}.{op}".lower()
                )
                return dict(total=len(names), operators=names[offset : offset + limit])
            name = name.removeprefix("bpy.ops.")
            parts = name.split(".")
            if len(parts) != 2 or not all(p.isidentifier() and not p.startswith("_") for p in parts):
                raise ValueError("Use an operator identifier such as mesh.primitive_cube_add")
            op = getattr(getattr(bpy.ops, parts[0]), parts[1])
            rna = op.get_rna_type()
            cls = bpy.types.Operator.bl_rna_get_subclass_py(rna.identifier)
            last_properties = (
                bpy.context.window_manager.operator_properties_last(rna.identifier) if cls else None
            )
            result = properties(rna, offset, limit, cls, last_properties)
            result["poll"] = op.poll()
            result["context_note"] = (
                "poll reflects the current worker context, not every possible editor override"
            )
            return result
        if not name.isidentifier() or name.startswith("_"):
            raise ValueError("Use a Blender RNA type identifier such as Object")
        typ = getattr(bpy.types, name, None)
        if typ is None or not hasattr(typ, "bl_rna"):
            raise ValueError("Unknown Blender RNA type")
        return properties(typ.bl_rna, offset, limit)
    raise ValueError("Unknown inspection query")
