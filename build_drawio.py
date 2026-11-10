#!/usr/bin/env python3
"""Build field-first Draw.io wiring diagrams from concise YAML."""
from __future__ import annotations
import argparse, copy, sys, uuid
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

EDGE_STYLE = 'edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=none;strokeWidth=2;jumpStyle=arc;'
RENDERER_REVISION = 16

TEXT_STYLE = 'text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontFamily=Courier New;fontSize=10;'


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('points_yml', type=Path)
    p.add_argument('--output', type=Path, default=Path('output.drawio'))
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    p.add_argument('--background', type=Path)
    p.add_argument('--ignore-terminal-reuse', action='store_true', help='Warn and continue when a terminal is used by more than one route')
    p.add_argument('--ignore-terminal-order', action='store_true', help='Warn and continue when via terminals are not in terminal_blocks order')
    return p.parse_args()


def load_yaml(path):
    with path.open(encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def find_named(root, name, suffix):
    stem = Path(name).stem.casefold()
    matches = [p for p in root.rglob('*' + suffix) if p.stem.casefold() == stem]
    if len(matches) != 1:
        raise ValueError(f'Expected one {Path(name).stem}{suffix} under {root}, found {matches}')
    return matches[0]


def load_root(path):
    tree = ET.parse(path)
    root = tree.getroot().find('.//root')
    if root is None:
        raise ValueError(f'Invalid Draw.io file: {path}')
    return root


def load_background(path):
    tree = ET.parse(path)
    model = tree.getroot().find('.//mxGraphModel')
    if model is None:
        raise ValueError(f'Invalid background Draw.io: {path}')
    return copy.deepcopy(model)


def nested_cell(element):
    return element.find('mxCell') if element.tag == 'object' else element


def geometry(element):
    g = nested_cell(element).find('mxGeometry')
    if g is None:
        raise ValueError(f'Missing geometry for {element.get("id")}')
    return g


def cell_index(root):
    result = {c.get('id'): c for c in root.iter('mxCell') if c.get('id')}
    for obj in root.iter('object'):
        c = obj.find('mxCell')
        if obj.get('id') and c is not None:
            result[obj.get('id')] = c
    return result


def absolute_xy(cell, index):
    g = cell.find('mxGeometry')
    x = float(g.get('x', 0)); y = float(g.get('y', 0))
    parent = cell.get('parent'); seen = set()
    while parent and parent not in ('0', '1') and parent not in seen:
        seen.add(parent)
        pc = index.get(parent)
        if pc is None:
            break
        pg = pc.find('mxGeometry')
        if pg is not None:
            x += float(pg.get('x', 0)); y += float(pg.get('y', 0))
        parent = pc.get('parent')
    return x, y


def flatten_template(root, prefix, origin_x, origin_y):
    index = cell_index(root)
    source = []
    for child in list(root):
        if child.tag not in ('object', 'mxCell'):
            continue
        c = nested_cell(child)
        oid = child.get('id')
        if not oid or oid in ('0', '1') or c is None or c.get('vertex') != '1' or 'group' in c.get('style', ''):
            continue
        x, y = absolute_xy(c, index)
        source.append((x, y, child))
    if not source:
        return [], {}
    min_x = min(x for x, _, _ in source); min_y = min(y for _, y, _ in source)
    output = []; id_map = {}
    for x, y, child in source:
        clone = copy.deepcopy(child)
        cc = nested_cell(clone); g = cc.find('mxGeometry')
        g.set('x', str(origin_x + x - min_x)); g.set('y', str(origin_y + y - min_y))
        cc.set('parent', '1')
        new_id = f'{prefix}_{child.get("id")}'
        clone.set('id', new_id); id_map[child.get('id')] = new_id; output.append(clone)
    return output, id_map


def template_height(root):
    """Bounding-box height of the flattened template (works with or without a group)."""
    items, _ = flatten_template(root, 'measure', 0, 0)
    bottom = 0.0
    for item in items:
        g = geometry(item)
        bottom = max(bottom, float(g.get('y', 0)) + float(g.get('height', 0)))
    return bottom


def set_value(element, value):
    if element.tag == 'object':
        element.set('label', value)
    nested_cell(element).set('value', value)


def get_element(root, element_id):
    for e in root.iter():
        if e.get('id') == element_id:
            return e
    raise ValueError(f'Element not found: {element_id}')


def centre(root, element_id):
    g = geometry(get_element(root, element_id))
    return float(g.get('x', 0)) + float(g.get('width', 0))/2, float(g.get('y', 0)) + float(g.get('height', 0))/2


PLACED_TERMINALS = []  # every terminal box already drawn
TERMINAL_STACK_PITCH = 32


def add_terminal(root, terminal_id, x, y, display_value=None):
    oid = 'term_' + terminal_id.replace('-', '_')
    if any(e.get('id') == oid for e in root):
        n = 2
        while any(e.get('id') == f'{oid}_{n}' for e in root):
            n += 1
        oid = f'{oid}_{n}'
    c = ET.Element('mxCell', {'id': oid, 'parent': '1', 'vertex': '1', 'value': display_value or terminal_id,
        'style': 'rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;fontSize=10;'})
    text = str(display_value or terminal_id)
    width = max(60, int(len(text) * 6.5) + 10)  # Courier New 10pt is ~6 px per char
    top = y - 6
    geo = ET.SubElement(c, 'mxGeometry', {'x': str(x), 'y': str(top), 'width': str(width), 'height': '12', 'as': 'geometry'})
    root.append(c)
    # The terminal being placed keeps its own row; any box already in the same
    # column that it would overlap is moved down one row pitch (32) below it.
    # Edges are attached to the boxes, so they follow automatically.
    new_box = {'x0': x, 'x1': x + width, 'top': top, 'geo': geo}
    pending = [new_box]
    PLACED_TERMINALS.append(new_box)
    while pending:
        anchor = pending.pop()
        for other in PLACED_TERMINALS:
            if other is anchor or other is new_box and anchor is new_box:
                continue
            if (anchor['x0'] < other['x1'] and anchor['x1'] > other['x0']
                    and anchor['top'] < other['top'] + 12 and anchor['top'] + 12 > other['top']):
                if other is new_box:
                    continue
                other['top'] = anchor['top'] + TERMINAL_STACK_PITCH
                other['geo'].set('y', str(other['top']))
                pending.append(other)
    return oid


def add_edge(root, source, target, points=None):
    c = ET.Element('mxCell', {'id': 'edge_' + uuid.uuid4().hex[:10], 'parent': '1', 'edge': '1',
        'source': source, 'target': target, 'value': '', 'style': EDGE_STYLE})
    g = ET.SubElement(c, 'mxGeometry', {'relative': '1', 'as': 'geometry'})
    if points:
        arr = ET.SubElement(g, 'Array', {'as': 'points'})
        for x, y in points:
            ET.SubElement(arr, 'mxPoint', {'x': str(x), 'y': str(y)})
    root.append(c)


def add_label(root, value, x, y):
    c = ET.Element('mxCell', {'id': 'lbl_' + uuid.uuid4().hex[:10], 'parent': '1', 'vertex': '1',
        'value': str(value), 'style': TEXT_STYLE})
    ET.SubElement(c, 'mxGeometry', {'x': str(x), 'y': str(y-14), 'width': '58', 'height': '14', 'as': 'geometry'})
    root.append(c)


def add_boundary(root, oid, x, label=None):
    c = ET.Element('mxCell', {'id': oid, 'parent': '1', 'edge': '1', 'value': '',
        'style': 'endArrow=none;dashed=1;html=1;rounded=0;defaultLabelBackgroundColor=page;'})
    g = ET.SubElement(c, 'mxGeometry', {'relative': '1', 'as': 'geometry'})
    ET.SubElement(g, 'mxPoint', {'x': str(x), 'y': '740', 'as': 'sourcePoint'})
    ET.SubElement(g, 'mxPoint', {'x': str(x), 'y': '40', 'as': 'targetPoint'})
    root.append(c)
    if label:
        t = ET.Element('mxCell', {'id': oid+'_label', 'parent': '1', 'vertex': '1', 'value': label,
            'style': 'text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;rounded=0;'})
        ET.SubElement(t, 'mxGeometry', {'x': str(x), 'y': '40', 'width': '180', 'height': '30', 'as': 'geometry'})
        root.append(t)


def find_controller_template(repo, configured):
    exact = list((repo/'drawioTemplates/controllers').rglob(Path(configured).name))
    if len(exact) == 1:
        return exact[0]
    required = {'point_type','point_secondary_type','point_name','primary_connection','secondary_connection'}
    matches = []
    for candidate in (repo/'drawioTemplates/controllers').rglob('*.drawio'):
        try:
            ids = {o.get('id') for o in load_root(candidate).iter('object')}
            if required <= ids:
                matches.append(candidate)
        except Exception:
            pass
    if len(matches) != 1:
        raise ValueError(f'Generic controller template not uniquely found: {matches}')
    return matches[0]


def main():
    a = parse_args(); repo = a.root.resolve(); project = load_yaml(a.points_yml.resolve())
    background = a.background or repo/'backgroundTemplates/Background.drawio'
    model = load_background(background); out = model.find('root')
    if out is None: raise ValueError('Background has no root')
    mx = ET.Element('mxfile', {'host': 'Electron'})
    controller_cfg = next(iter(project['controllers'].values()))
    diagram = ET.SubElement(mx, 'diagram', {'name': controller_cfg.get('drawing_name','Field First Test'), 'id': 'field-first-test'})
    diagram.append(model)

    layout = project.get('layout', {})
    controller_origin = layout.get('controller_origin', {})
    controller_x = float(controller_origin.get('x', layout.get('controller_x', 40)))
    start_y = float(controller_origin.get('y', layout.get('start_y', 80)))
    point_pitch = float(layout.get('point_pitch_y', layout.get('point_pitch', 96)))
    terminal_x0 = float(layout.get('terminal_origin_x', layout.get('terminal_x', 442)))
    terminal_pitch = float(layout.get('terminal_column_spacing', layout.get('terminal_pitch', 141)))
    device_x = float(layout.get('field_device_x', layout.get('device_x', 1230)))
    device_pitch = float(layout.get('field_device_pitch_y', layout.get('device_pitch', 97)))

    terminal_blocks = layout.get('terminal_blocks', {})
    terminal_block_orders = {
        str(name).upper(): int(config.get('order', config))
        for name, config in terminal_blocks.items()
    }
    default_terminal_order = terminal_block_orders.get('OTHER', len(terminal_block_orders) + 1)

    # Blocks may share an order/column; terminals that would overlap are stacked vertically.
    if 'OTHER' not in terminal_block_orders and terminal_block_orders:
        _max_listed = max(terminal_block_orders.values())
        default_terminal_order = _max_listed + 1
    _warned_blocks = set()

    terminal_owner = {}

    def claim_terminal(terminal_id, owner):
        block = terminal_block_name(terminal_id)
        if terminal_block_orders and block not in terminal_block_orders and block not in _warned_blocks:
            _warned_blocks.add(block)
            print(f'WARNING: terminal block {block} is not listed in layout.terminal_blocks; '
                  f'placing it in the OTHER column (x={terminal_column_x(terminal_id):g}).', file=sys.stderr)
        prev = terminal_owner.setdefault(str(terminal_id), owner)
        if prev != owner:
            message = f'Terminal {terminal_id} used by both {prev} and {owner}'
            if a.ignore_terminal_reuse:
                print(f'WARNING: {message}', file=sys.stderr)
            else:
                raise ValueError(message + '\n  Re-run with --ignore-terminal-reuse to continue.')

    def terminal_block_name(terminal_id):
        terminal_id = str(terminal_id)
        return terminal_id.split('-', 1)[0].upper() if '-' in terminal_id else 'OTHER'

    def terminal_column_x(terminal_id):
        block = terminal_block_name(terminal_id)
        order = terminal_block_orders.get(block, default_terminal_order)
        return terminal_x0 + (order - 1) * terminal_pitch

    def validate_terminal_order(tag, interface_name, via):
        if len(via) < 2:
            return
        orders = [terminal_block_orders.get(terminal_block_name(t), default_terminal_order) for t in via]
        if orders == sorted(orders):
            return
        suggested = [t for _, t in sorted(zip(orders, via), key=lambda item: item[0])]
        message = (
            f'{tag}.{interface_name}: via terminals are not in terminal_blocks order.\n'
            f'  Current:   via: [{", ".join(map(str, via))}]\n'
            f'  Suggested: via: [{", ".join(map(str, suggested))}]'
        )
        if a.ignore_terminal_order:
            print(f'WARNING: {message}', file=sys.stderr)
        else:
            raise ValueError(message + '\n  Re-run with --ignore-terminal-order to continue without changing the YAML.')

    add_boundary(out, 'region_mssb_start', 1020, 'MSSB')
    add_boundary(out, 'region_field_start', 1200, 'Field')
    add_boundary(out, 'region_field_end', 1380)

    controller_defs = {name: load_yaml(find_named(repo/'controllerTemplates', name, '.yml')) for name in {c['template'] for c in project['controllers'].values()}}
    device_defs = {name: load_yaml(find_named(repo/'deviceTemplates', name, '.yml')) for name in {d['template'] for d in project['devices'].values()}}
    generic_file = find_controller_template(repo, controller_cfg.get('point_template','GenericController.drawio'))
    generic_root = load_root(generic_file)

    point_order = []
    for device in project['devices'].values():
        for iface, wire in device['wiring'].items():
            destination = wire.get('to', {})
            if 'point' in destination:
                key = (device.get('controller', next(iter(project['controllers']))), destination['point'])
                if key not in point_order: point_order.append(key)

    point_targets = {}
    shared_controller_targets = {}
    shared_owner_by_key = {}
    point_y = {}
    current_point_y = start_y
    previous_device_height = 0
    # Uniform gap between the bottom of the taller of (controller point, field device)
    # and the top of the next point. Default 32 = 96 pitch for a 64 px point.
    controller_row_height = template_height(generic_root)
    point_gap = float(layout.get('point_gap', layout.get('point_gap_y', point_pitch - controller_row_height)))
    for index, (controller_id, point_id) in enumerate(point_order):
        if index:
            current_point_y += max(controller_row_height, previous_device_height) + point_gap
        point_y[(controller_id, point_id)] = current_point_y
        controller = controller_defs[project['controllers'][controller_id]['template']]
        pd = controller['points'][point_id]; interfaces = pd['interfaces']
        primary = next((n for n in interfaces if n in ('signal','input','output','supply')), next(iter(interfaces)))
        secondary = next((n for n in interfaces if n in ('common','output_common','supply_common')), None)
        items, ids = flatten_template(generic_root, f'{controller_id}_{point_id}', controller_x, current_point_y)
        # Measure the tallest field device wired to this point so the next
        # point starts below its lowest part plus the standard 8 px gap.
        previous_device_height = 0
        for candidate in project['devices'].values():
            candidate_controller = candidate.get('controller', next(iter(project['controllers'])))
            wired_points = [w.get('to', {}).get('point') for w in candidate['wiring'].values()]
            if candidate_controller == controller_id and point_id in wired_points:
                candidate_def = device_defs[candidate['template']]['device_template']['drawing']
                candidate_root = load_root(find_named(repo/'drawioTemplates/devices', Path(candidate_def['template']).stem, '.drawio'))
                previous_device_height = max(previous_device_height, template_height(candidate_root))
        lookup = {e.get('id'): e for e in items}
        set_value(lookup[ids['point_type']], pd.get('display_name', point_id))
        set_value(lookup[ids['primary_connection']], str(interfaces[primary].get('terminal','')))
        secondary_config = interfaces.get(secondary,{}) if secondary else {}
        secondary_shared = secondary_config.get('shared_connection')
        if secondary and not secondary_shared:
            terminal = secondary_config.get('terminal')
            role = secondary_config.get('role', secondary)
            if terminal is not None:
                secondary_shared = f'{role}:terminal:{terminal}'
        duplicate_secondary = bool(secondary_shared and (controller_id, secondary_shared) in shared_controller_targets)
        if secondary and not duplicate_secondary:
            set_value(lookup[ids['point_secondary_type']], interfaces[secondary].get('display_name', secondary))
            set_value(lookup[ids['secondary_connection']], str(interfaces[secondary].get('terminal','')))
        # A repeated shared common is not drawn as a second controller row.
        # Keep the point's primary row and description, remove the duplicate
        # secondary type/connection objects, and resolve wiring to the first one.
        if duplicate_secondary:
            hidden = {ids['point_secondary_type'], ids['secondary_connection']}
            items = [item for item in items if item.get('id') not in hidden]
        for item in items: out.append(item)
        point_targets[(controller_id, point_id, primary)] = ids['primary_connection']
        if secondary:
            if duplicate_secondary:
                point_targets[(controller_id, point_id, secondary)] = shared_controller_targets[(controller_id, secondary_shared)]
            else:
                point_targets[(controller_id, point_id, secondary)] = ids['secondary_connection']
                if secondary_shared:
                    shared_controller_targets[(controller_id, secondary_shared)] = ids['secondary_connection']
                    shared_owner_by_key[(controller_id, secondary_shared)] = point_id

    device_targets = {}
    for index, (tag, device) in enumerate(project['devices'].items()):
        definition = device_defs[device['template']]['device_template']; drawing = definition['drawing']
        device_root = load_root(find_named(repo/'drawioTemplates/devices', Path(drawing['template']).stem, '.drawio'))
        # Keep field devices aligned to their associated controller point.
        point_destinations = [
            wire.get('to', {}).get('point')
            for wire in device['wiring'].values()
            if wire.get('to', {}).get('point')
        ]
        first_point = point_destinations[0] if point_destinations else None
        point_key = (device.get('controller', next(iter(project['controllers']))), first_point)
        device_top = point_y.get(point_key, start_y + index * point_pitch)
        items, ids = flatten_template(device_root, f'dev_{tag}', device_x, device_top)
        lookup = {e.get('id'): e for e in items}; set_value(lookup[ids[drawing['label_id']]], tag)
        for item in items: out.append(item)
        device_targets[tag] = {name: ids[cfg['drawio_id']] for name,cfg in drawing['interfaces'].items()}
        controller_id = device.get('controller', next(iter(project['controllers'])))
        desc_id = f'{controller_id}_{first_point}_point_name'
        try: set_value(get_element(out, desc_id), f"<b>{device.get('equipment','')} - {tag}</b><br>{device.get('description','')}")
        except ValueError: pass

    terminals = {}
    common_route_cache = {}
    common_source_use_count = {}
    common_branch_count = {}
    for tag, device in project['devices'].items():
        controller_id = device.get('controller', next(iter(project['controllers'])))
        for iface, wire in device['wiring'].items():
            destination = wire.get('to', {})
            target_device = device_targets[tag][iface]
            _, device_y = centre(out, target_device)
            via = list(wire.get('via', [])); labels = list(wire.get('labels', []))
            validate_terminal_order(tag, iface, via)
            terminal_destination = destination.get('terminal') or destination.get('terminal_strip')
            if terminal_destination:
                if iface not in ('supply', 'common', 'supply_common', 'output_common'):
                    raise ValueError(f'{tag}.{iface}: terminal destination only supports supply/common')
                # If the destination is repeated as the first via terminal,
                # treat it as the cable endpoint and omit the controller-side segment.
                if via and via[0] == terminal_destination:
                    via = via[1:]
                    if len(labels) == len(via) + 2:
                        labels = labels[1:]
                if len(labels) != len(via) + 1:
                    raise ValueError(f'{tag}.{iface}: expected {len(via)+1} labels for terminal destination')
                row_y = device_y
                claim_terminal(terminal_destination, f'{tag}.{iface}')
                source = add_terminal(out, terminal_destination, terminal_column_x(terminal_destination), row_y)
                source_element = get_element(out, source)
                source_id = f'{source}_{tag}_{iface}_destination'
                source_element.set('id', source_id)
                source = source_id
                source_x, source_y = centre(out, source)
                is_common_interface = False
                print(f'External terminal destination: {tag}.{iface} starts at {terminal_destination}')
            else:
                point = destination['point']; controller_iface = destination.get('interface', iface)
                source = point_targets[(controller_id, point, controller_iface)]
                source_x, source_y = centre(out, source)
                is_common_interface = controller_iface in ('common', 'output_common', 'supply_common')
                common_use_index = common_source_use_count.get(source, 0) if is_common_interface else 0
                if is_common_interface:
                    common_source_use_count[source] = common_use_index + 1
                row_y = source_y
                if len(labels) != len(via)+1:
                    raise ValueError(f'{tag}.{iface}: labels must equal via terminals plus one')

            # The same resolved controller common and same terminal path are one
            # physical common route. Draw the route once, then branch subsequent
            # field devices from the right-hand end of that route.
            common_route_key = (source, tuple(via)) if is_common_interface else None
            is_shared_branch = bool(common_route_key and common_route_key in common_route_cache)
            if is_shared_branch:
                previous = common_route_cache[common_route_key]
                print(f'Shared branch: {tag}.{iface} reuses common route {source} at x={layout.get("shared_branch_x", 1130)}')
            else:
                previous = source
                row_y = source_y
                for i, terminal_id in enumerate(via):
                    # Terminal blocks have fixed columns. Every X4 terminal
                    # aligns with X4, every X3 with X3, every X2 with X2, etc.,
                    # regardless of the order in the wire's via list.
                    x = terminal_column_x(terminal_id)
                    claim_terminal(terminal_id, f'{tag}.{iface}')
                    oid = add_terminal(out, terminal_id, x, row_y)
                    element = get_element(out, oid)
                    unique_id = f'{oid}_{tag}_{iface}'
                    element.set('id', unique_id)
                    target_terminal = unique_id
                    add_edge(out, previous, target_terminal)
                    add_label(out, labels[i], x-65, row_y)
                    previous = target_terminal
                if common_route_key:
                    common_route_cache[common_route_key] = previous

            target = target_device
            if is_shared_branch:
                # Join only when the ordered common path matches exactly.
                # The first additional connection branches 28 px after the
                # right edge of the last terminal. Further branches move left
                # by 4 px each: 28, 24, 20, 16, ...
                _, last_terminal_y = centre(out, previous)
                last_geometry = geometry(get_element(out, previous))
                last_terminal_right = float(last_geometry.get('x', 0)) + float(last_geometry.get('width', 0))
                branch_index = common_branch_count.get(common_route_key, 0)
                base_offset = float(layout.get('common_branch_offset', 28))
                step = float(layout.get('common_branch_step', 4))
                minimum_offset = float(layout.get('common_branch_min_offset', 4))
                branch_offset = base_offset - branch_index * step
                if branch_offset < minimum_offset:
                    raise ValueError(
                        f'No free branch position for {tag}.{iface}; offsets '
                        f'{base_offset:g}, {base_offset-step:g}, ... reached minimum {minimum_offset:g}'
                    )
                common_branch_count[common_route_key] = branch_index + 1
                branch_x = last_terminal_right + branch_offset
                print(
                    f'Common branch: {tag}.{iface} path={list(via)} '
                    f'offset={branch_offset:g} x={branch_x:g}'
                )
                add_edge(out, previous, target, [(branch_x, source_y), (branch_x, device_y)])
                final_label_x = branch_x + float(layout.get('common_branch_label_offset', 4))
            else:
                add_edge(out, previous, target)
                rightmost_terminal_x = max(
                    (terminal_column_x(terminal_id) for terminal_id in via),
                    default=terminal_column_x(terminal_destination) if terminal_destination else terminal_x0,
                )
                final_label_x = rightmost_terminal_x + 80
            add_label(out, labels[-1], final_label_x, device_y)


    # Controller-only auxiliary routes, such as an input common/reference
    # landed on a terminal for grounding. These routes do not connect to a
    # field-device interface.
    for tag, device in project['devices'].items():
        controller_id = device.get('controller', next(iter(project['controllers'])))
        for route_name, route in device.get('auxiliary_wiring', {}).items():
            source_cfg = route.get('from', {})
            destination_cfg = route.get('to', {})
            point = source_cfg.get('point')
            interface_name = source_cfg.get('interface', 'common')
            destination_terminal = destination_cfg.get('terminal')
            if not point:
                raise ValueError(f'{tag}.auxiliary_wiring.{route_name}: from.point is required')
            if not destination_terminal:
                raise ValueError(f'{tag}.auxiliary_wiring.{route_name}: to.terminal is required')
            source_key = (controller_id, point, interface_name)
            if source_key not in point_targets:
                raise ValueError(
                    f'{tag}.auxiliary_wiring.{route_name}: controller source '
                    f'{controller_id}.{point}.{interface_name} was not found'
                )

            via = list(route.get('via', []))
            complete_terminal_path = via + [destination_terminal]
            validate_terminal_order(tag, f'auxiliary_wiring.{route_name}', complete_terminal_path)
            labels = list(route.get('labels', []))
            if len(labels) != len(complete_terminal_path):
                raise ValueError(
                    f'{tag}.auxiliary_wiring.{route_name}: labels must equal via '
                    f'terminals plus destination terminal; expected '
                    f'{len(complete_terminal_path)}, got {len(labels)}'
                )

            previous = point_targets[source_key]
            _, row_y = centre(out, previous)
            for i, terminal_id in enumerate(complete_terminal_path):
                x = terminal_column_x(terminal_id)
                claim_terminal(terminal_id, f'{tag}.aux.{route_name}')
                oid = add_terminal(out, terminal_id, x, row_y)
                element = get_element(out, oid)
                unique_id = f'{oid}_{tag}_aux_{route_name}_{i}'
                element.set('id', unique_id)
                add_edge(out, previous, unique_id)
                add_label(out, labels[i], x - 65, row_y)
                previous = unique_id
            print(
                f'Auxiliary route: {tag}.{route_name} '
                f'{point}.{interface_name} -> {destination_terminal}'
            )

    ET.indent(mx, space='  '); a.output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(mx).write(a.output, encoding='utf-8', xml_declaration=True)
    print(f'Renderer revision: R{RENDERER_REVISION}')
    print(f'Created {a.output}')

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr); raise SystemExit(1)
