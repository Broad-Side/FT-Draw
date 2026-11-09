#!/usr/bin/env python3
"""Build field-first Draw.io wiring diagrams from concise YAML."""
from __future__ import annotations
import argparse, copy, sys, uuid
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

EDGE_STYLE = 'edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=none;strokeWidth=2;jumpStyle=arc;'
RENDERER_REVISION = 7

TEXT_STYLE = 'text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontFamily=Courier New;fontSize=10;'


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('points_yml', type=Path)
    p.add_argument('--output', type=Path, default=Path('output.drawio'))
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    p.add_argument('--background', type=Path)
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


def add_terminal(root, terminal_id, x, y, display_value=None):
    oid = 'term_' + terminal_id.replace('-', '_')
    c = ET.Element('mxCell', {'id': oid, 'parent': '1', 'vertex': '1', 'value': display_value or terminal_id,
        'style': 'rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;fontSize=10;'})
    ET.SubElement(c, 'mxGeometry', {'x': str(x), 'y': str(y-6), 'width': '60', 'height': '12', 'as': 'geometry'})
    root.append(c)
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
    controller_x = float(layout.get('controller_x', 40)); start_y = float(layout.get('start_y', 80)); point_pitch = float(layout.get('point_pitch', 96))
    terminal_x0 = float(layout.get('terminal_x', 442)); terminal_pitch = float(layout.get('terminal_pitch', 141))
    device_x = float(layout.get('device_x', 1230)); device_pitch = float(layout.get('device_pitch', 97))

    add_boundary(out, 'region_mssb_start', 1020, 'MSSB')
    add_boundary(out, 'region_field_start', 1200, 'Field')
    add_boundary(out, 'region_field_end', 1380)
    # Vertical common channels occupy the strip immediately left of Field.
    channel_title = ET.Element('mxCell', {
        'id': 'vertical_channel_zone_label', 'parent': '1', 'vertex': '1',
        'value': 'Channels for vertical lines',
        'style': 'text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=8;'
    })
    ET.SubElement(channel_title, 'mxGeometry', {
        'x': str(float(layout.get('vertical_channel_left', 1030))), 'y': '65',
        'width': str(float(layout.get('vertical_channel_right', 1130)) - float(layout.get('vertical_channel_left', 1030))),
        'height': '20', 'as': 'geometry'
    })
    out.append(channel_title)

    controller_defs = {name: load_yaml(find_named(repo/'controllerTemplates', name, '.yml')) for name in {c['template'] for c in project['controllers'].values()}}
    device_defs = {name: load_yaml(find_named(repo/'deviceTemplates', name, '.yml')) for name in {d['template'] for d in project['devices'].values()}}
    generic_file = find_controller_template(repo, controller_cfg.get('point_template','GenericController.drawio'))
    generic_root = load_root(generic_file)

    point_order = []
    for device in project['devices'].values():
        for iface, wire in device['wiring'].items():
            key = (device.get('controller', next(iter(project['controllers']))), wire['to']['point'])
            if key not in point_order: point_order.append(key)

    point_targets = {}
    shared_controller_targets = {}
    shared_keys_by_interface = {}
    shared_owner_by_key = {}
    for index, (controller_id, point_id) in enumerate(point_order):
        controller = controller_defs[project['controllers'][controller_id]['template']]
        pd = controller['points'][point_id]; interfaces = pd['interfaces']
        primary = next((n for n in interfaces if n in ('signal','input','output','supply')), next(iter(interfaces)))
        secondary = next((n for n in interfaces if n in ('common','output_common','supply_common')), None)
        items, ids = flatten_template(generic_root, f'{controller_id}_{point_id}', controller_x, start_y + index*point_pitch)
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
            if secondary_shared:
                shared_keys_by_interface[(controller_id, point_id, secondary)] = (controller_id, secondary_shared)

    device_targets = {}
    for index, (tag, device) in enumerate(project['devices'].items()):
        definition = device_defs[device['template']]['device_template']; drawing = definition['drawing']
        device_root = load_root(find_named(repo/'drawioTemplates/devices', Path(drawing['template']).stem, '.drawio'))
        items, ids = flatten_template(device_root, f'dev_{tag}', device_x, start_y + index*device_pitch)
        lookup = {e.get('id'): e for e in items}; set_value(lookup[ids[drawing['label_id']]], tag)
        for item in items: out.append(item)
        device_targets[tag] = {name: ids[cfg['drawio_id']] for name,cfg in drawing['interfaces'].items()}
        first_point = next(iter(device['wiring'].values()))['to']['point']; controller_id = device.get('controller', next(iter(project['controllers'])))
        desc_id = f'{controller_id}_{first_point}_point_name'
        try: set_value(get_element(out, desc_id), f"<b>{device.get('equipment','')} - {tag}</b><br>{device.get('description','')}")
        except ValueError: pass

    terminals = {}
    common_route_cache = {}
    common_source_use_count = {}
    vertical_channel_by_source = {}
    used_vertical_channels = []
    revision_marker_used = False
    for tag, device in project['devices'].items():
        controller_id = device.get('controller', next(iter(project['controllers'])))
        for iface, wire in device['wiring'].items():
            point = wire['to']['point']; controller_iface = wire['to'].get('interface', iface)
            source = point_targets[(controller_id, point, controller_iface)]
            source_x, source_y = centre(out, source)
            is_common_interface = controller_iface in ('common', 'output_common', 'supply_common')
            common_use_index = common_source_use_count.get(source, 0) if is_common_interface else 0
            if is_common_interface:
                common_source_use_count[source] = common_use_index + 1
            target_device = device_targets[tag][iface]
            _, device_y = centre(out, target_device)
            via = wire.get('via', []); labels = wire.get('labels', [])
            if len(labels) != len(via)+1:
                raise ValueError(f'{tag}.{iface}: labels must equal via terminals plus one')

            # The same resolved controller common and same terminal path are one
            # physical common route. Draw the route once, then branch subsequent
            # field devices from the right-hand end of that route.
            common_route_key = source if is_common_interface else None
            is_shared_branch = bool(common_route_key and common_route_key in common_route_cache)
            if is_shared_branch:
                previous = common_route_cache[common_route_key]
                print(f'Shared branch: {tag}.{iface} reuses common route {source} at x={layout.get("shared_branch_x", 1130)}')
            else:
                previous = source
                row_y = source_y
                for i, terminal_id in enumerate(via):
                    x = terminal_x0 + i*terminal_pitch
                    terminal_key = (tag, iface, terminal_id)
                    display_value = terminal_id
                    if not revision_marker_used:
                        display_value = f'{terminal_id} [R{RENDERER_REVISION}]'
                        revision_marker_used = True
                    oid = add_terminal(out, terminal_id, x, row_y, display_value)
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
                # Allocate one vertical channel per shared controller common.
                # Channels run right-to-left inside the nominated channel zone,
                # preventing unrelated common risers from drawing on top of one
                # another. Every branch using the same common reuses its channel.
                channel_right = float(layout.get('vertical_channel_right', 1130))
                channel_spacing = float(layout.get('vertical_channel_spacing', 20))
                channel_left = float(layout.get('vertical_channel_left', 1030))
                channel_key = common_route_key
                if channel_key not in vertical_channel_by_source:
                    channel_index = len(vertical_channel_by_source)
                    branch_x = channel_right - channel_index * channel_spacing
                    if branch_x < channel_left:
                        raise ValueError(
                            f'No free vertical common channel for {tag}.{iface}; '
                            f'channel zone {channel_left}..{channel_right} with spacing {channel_spacing} is full'
                        )
                    vertical_channel_by_source[channel_key] = branch_x
                    used_vertical_channels.append(branch_x)
                else:
                    branch_x = vertical_channel_by_source[channel_key]
                print(f'Vertical channel: {tag}.{iface} uses x={branch_x:g} for {source}')
                add_edge(out, previous, target, [(branch_x, source_y), (branch_x, device_y)])
                final_label_x = branch_x + float(layout.get('vertical_channel_label_offset', 4))
            else:
                add_edge(out, previous, target)
                final_label_x = terminal_x0 + max(len(via)-1,0)*terminal_pitch + 80
            add_label(out, labels[-1], final_label_x, device_y)

    ET.indent(mx, space='  '); a.output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(mx).write(a.output, encoding='utf-8', xml_declaration=True)
    print(f'Renderer revision: R{RENDERER_REVISION}')
    print(f'Created {a.output}')

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr); raise SystemExit(1)
