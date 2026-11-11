# FT-Draw Schema Reference

Nov 11, 2026 · @Dean

## Overview

`build_drawing.py` turns one project `points.yml` (schema\_version 4) into a Draw.io wiring drawing. It reads three kinds of input:

| File | Where | What it holds |
| --- | --- | --- |
| `points.yml` | `project/<name>/` | Controllers used, layout settings, and every point with its wiring |
| Controller template | `controllerTemplates/<model>.yml` | The controller's IO points, their terminals and which ones share a terminal |
| Background | `backgroundTemplates/Background.drawio` | Title block and border, drawn on a layer under the wiring |

&#91;embedded content: build inputs and output layers\]

`points.yml` decides what is drawn; the controller template supplies terminal numbers and shared commons; the background sits under everything.

Run it from the repo root:

```bash
python3 build_drawing.py project/testProject/points.yml -o output.drawio
```

| Option | Effect |
| --- | --- |
| `-o`, `--output` | Output file (default `output.drawio`) |
| `--controller-library <dir>` | Extra folder to search for controller templates; repeatable |
| `--background <file>` | Use a different background drawing |
| `--no-background` | Draw without a background |
| `--ignore-terminal-order` | Warn instead of stopping when a route's terminals are out of order |

`controllers.yml`, `panelDetails.yml`, `cables.yml`, `terminalBlocks.yml` and `terminal_strips.yml` in the project folder are not read by the builder yet.

## points.yml: top level

A `points.yml` has four top-level keys. `schema_version`, `controllers` and `points` are required.

```yaml
schema_version: 4
controllers: { ... }
layout: { ... }
points: { ... }
```

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `schema_version` | number | Yes | Must be `4` |
| `controllers` | map | Yes | One entry per controller, keyed by its tag (e.g. `SNC01`) |
| `layout` | map | No | Drawing positions and terminal block order (see Layout settings) |
| `points` | map | Yes | One entry per IO point, keyed by its tag (e.g. `TT12-OAT`). Drawn in file order |

### controllers.\<tag>

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `template` | text | Yes | Controller template name, e.g. `JCI_SNC25152`. Matched to a `.yml` file name, ignoring case; a vendor prefix is allowed either way (`SNC25152` finds `JCI_SNC25152.yml`) |
| `drawing_name` | text | No | Page name in Draw.io. Taken from the first controller; default `Field Connections` |
| `point_template` | text | No | Not used by the builder |

Templates are searched in this order: each `--controller-library` folder, the folder holding `points.yml`, `controllerTemplates/`, then the repo root.

## points.yml: a point

Each entry under `points:` is one controller IO wired to one field device. Its key is the point tag shown on the drawing.

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `controller` | text | Yes | A tag listed under `controllers` |
| `equipment` | text | No | Shown before the tag, e.g. `AHU04 - TT12-OAT` |
| `description` | text | No | Second line under the tag |
| `controller_point.point` | text | Yes | IO name in the controller template, e.g. `UI1`. Each IO can be used by only one point |
| `field_device.label` | text | No | Device name drawn at the right; default is the point tag |
| `field_device.interfaces` | map | Yes | The device's terminals, at least one |
| `wiring` | map | No | The connections to draw, one entry per wire run (see Wiring routes) |

### field\_device.interfaces.\<name>

The interface name (`signal`, `supply`, `sig`, `pwr`...) is your own; wiring refers to it with `field_interface`.

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `order` | whole number ≥ 1 | Yes | Row on the device, top first. Must be unique within the device |
| `label` | text | No | Text in the terminal box; default is the name in capitals |

## Wiring routes

Every entry under `wiring:` is drawn. A route runs from a start, through zero or more panel terminals, to an end; each wire between two of those gets one label.

```yaml
wiring:
  signal:                                   # route name: your own
    from: {point: UI1, interface: signal}   # controller terminal
    via: [X8-001B]                          # panel terminals in between
    labels: [8-001B, 8-001B]                # one per wire = len(via) + 1
    to: {field_interface: signal}           # field device terminal
```

| Key | Allowed forms | Rule |
| --- | --- | --- |
| `from` | `{point, interface}` or `{terminal}` | `interface` must exist on that IO in the controller template |
| `via` | list of terminal names | May be empty `[]` |
| `labels` | list of text | Exactly `len(via) + 1` entries. An empty entry draws no label |
| `to` | `{field_interface}` or `{terminal}` | `field_interface` must be declared under the point's `field_device.interfaces` |

### Terminal names and order

A terminal is named `<block>-<number>`, e.g. `X8-001B`. The part before the first dash is the terminal block, and `layout.terminal_blocks` gives each block its column. A name with no dash belongs to `other`.

- Terminals on a route must run controller side to field side: block order may stay the same or rise, never fall.
- A route between two panel terminals that is written backwards (e.g. `from: X8-002B`, `to: X4-001`) is flipped automatically, with its `via` and `labels`, and a note is printed.
- Any other out-of-order route stops the build unless `--ignore-terminal-order` is used.
- A terminal name used in several routes is drawn once; later routes wire back to it.

### Shared controller terminals

When the controller template gives two IO the same `shared_connection` (UI1 and UI2 share `C 1/2`), that terminal is drawn once, on the first point that uses it. A later point may start a route from either IO's interface, e.g. `from: {point: UI1, interface: common}` on the UI2 point. If the second IO needs no wire of its own, leave the route out of `points.yml`.

A route identical to an earlier one (same start, `via`, `labels` and end) is still drawn, and a warning is printed in case it was copied by mistake.

## Controller template

One YAML file per controller model describes its IO, so the same `points.yml` format works for any BMS or BAS controller. Add a new controller by adding a new file to `controllerTemplates/`.

```yaml
schema_version: 1
controller:
  manufacturer: Johnson Controls
  model: SNC25152

points:
  UI1:
    point_type: UI
    display_name: UI1
    interfaces:
      signal:
        role: input
        terminal: 1
      common:
        role: signal_common
        terminal: 2
        display_name: C 1/2
        shared_connection: UI_COMMON_1_2
```

### points.\<IO>

`points` may sit at the top level or under `controller:`. The key (`UI1`, `BO3`, `CO1`) is what `controller_point.point` refers to.

| Key | Used by builder | Meaning |
| --- | --- | --- |
| `display_name` | Yes | Text in the first row box; default is the IO key |
| `interfaces` | Yes | The IO's terminals, drawn one row each in the order listed |
| `point_type` | No | UI, BI, BO, AO, CO, SUPPLY — for reference |
| `drawing_layout` | No | For reference |

### points.\<IO>.interfaces.\<name>

| Key | Used by builder | Meaning |
| --- | --- | --- |
| `terminal` | Yes | Controller terminal number shown in the small box |
| `display_name` | Yes | Row text for the second and later rows. Default: the name shortened, e.g. `output_common` → `OUT COM` |
| `shared_connection` | Yes | Same value on two IO = one physical terminal. Drawn once and reused |
| `role` | No | For reference |

`controller`, `drawing`, `networks` and `capabilities` sections are for reference and are not read by the builder.

## Layout settings

Everything under `layout:` is optional. Values are Draw.io units (px).

| Key | Default | Meaning |
| --- | --- | --- |
| `controller_origin` | `{x: 40, y: 80}` | Top-left of the first point |
| `row_height` | 32 | Spacing between rows inside a point |
| `point_pitch_y` | 96 | Minimum distance from one point's top to the next |
| `point_vertical_gap` | 8 | Clear space under the lowest row of a point |
| `terminal_origin_x` | 435 | x of terminal column 1 |
| `terminal_column_spacing` | 210 | Distance between terminal columns |
| `field_device_x` | 1228 | x of the field device terminal boxes |
| `field_device_width` | 150 | Field terminal box plus device name |
| `field_device_jog_offset` | 12 | How far left of the field device (and right of the controller) a wire bends when it changes row |
| `terminal_blocks` | none | Column for each terminal block: `X8: {order: 3}`. Lower order = closer to the controller. `other` covers names with no block; default is one past the highest |
| `background` | `backgroundTemplates/Background.drawio` | Background drawing, relative to the repo root |

### How the page is laid out

- Points are drawn top to bottom in file order. A point is as tall as its longest list: controller rows, field device rows, or wiring rows used.
- The next point starts at `max(point_pitch_y, rows × row_height + point_vertical_gap)` below the previous one.
- A terminal's column is `terminal_origin_x + (order − 1) × terminal_column_spacing`. Its row is the row of the first route that uses it.

### How a wiring route gets its row

Rows are chosen by the builder, so the order routes are written in `points.yml` does not change the drawing.

1. Routes starting at the controller take that controller terminal's row.
2. Routes starting at a panel terminal take the row of the field terminal they end on, if that row is free.
3. Otherwise the route takes the first row where it does not overlap another route left to right.

Wire labels sit on their own wire, halfway between the two boxes it joins.

## Errors, warnings and notes

An ERROR stops the build and writes no drawing (exit code 2). A WARNING or NOTE is printed and the drawing is still made. Messages name the point and route, e.g. `TT12-OAT.wiring.signal`.

| Message starts with | Level | Fix |
| --- | --- | --- |
| `schema_version must be 4` | Error | Set `schema_version: 4` |
| `points: must contain at least one point` | Error | Add a point under `points:` |
| `controllers: must list at least one controller` | Error | Add a controller under `controllers:` |
| `controller template ... not found` | Error | Check `template:` matches a file in `controllerTemplates/` |
| `no 'points' found in controller template` | Error | Add a `points:` section to the template |
| `unknown controller` | Error | Use a tag listed under `controllers:` |
| `controller_point.point is required` | Error | Add `controller_point: {point: UI1}` |
| `point ... does not exist on ...'s controller template` | Error | Use an IO key from the template |
| `... is already used by ...` | Error | Two points use the same IO; pick another |
| `field_device.interfaces is required` | Error | Add at least one device terminal |
| `field_device.interfaces orders must be unique whole numbers >= 1` | Error | Give each device terminal its own `order` |
| `missing from, via, labels, to` | Error | Every route needs all four keys |
| `via and labels must be lists` | Error | Use `[...]`, even when empty |
| `needs N labels (one per wire)` | Error | `labels` = `len(via) + 1` |
| `from must be {point, interface} or {terminal}` | Error | Fix the `from` form |
| `to must be {field_interface} or {terminal}` | Error | Fix the `to` form |
| `... has no interface ...` | Error | Use an interface listed on that IO in the template |
| `from.point is ... but controller_point is ... and they don't share ...` | Error | Start from the point's own IO, or one sharing that terminal |
| `field_interface ... not declared` | Error | Add it under `field_device.interfaces` |
| `terminals are not in terminal_blocks order` | Error | Reorder as suggested, or `--ignore-terminal-order` (then Warning) |
| `not an uncompressed draw.io file` | Error | In draw.io: File > Properties > untick Compressed, save |
| `background file not found` | Error | Fix `layout.background` or `--background` |
| `... is identical to ... drawn anyway` | Warning | Remove the copy if it isn't a real second wire |
| `... drawn as A -> B (written the other way round)` | Note | None needed; swap `from` and `to` to silence it |

## Worked example

This is the test project's first point: a 2-wire outside air sensor on UI1, with a 24 VDC supply and the common bridged to 0 V. It draws on four rows.

```yaml
schema_version: 4

controllers:
  SNC01:
    template: JCI_SNC25152
    drawing_name: SNC01 - UI Connections

layout:
  controller_origin: {x: 40, y: 80}
  point_pitch_y: 96
  point_vertical_gap: 8
  terminal_origin_x: 435
  terminal_column_spacing: 210
  field_device_x: 1228
  terminal_blocks:
    X3: {order: 1}     # 24 VDC
    X4: {order: 1}     # 24 VDC common
    X5: {order: 2}     # 24 VAC
    X6: {order: 2}     # 24 VAC common
    X7: {order: 3}     # Digital I/O
    X8: {order: 3}     # Analog I/O
    other: {order: 4}  # MSSB or other

points:
  TT12-OAT:
    controller: SNC01
    equipment: AHU04
    description: Outside Air Temperature
    controller_point:
      point: UI1
    field_device:
      label: TT12-OAT
      interfaces:
        signal: {order: 1, label: SIG}
        supply: {order: 2, label: "+"}
    wiring:
      signal:
        from: {point: UI1, interface: signal}
        via: [X8-001B]
        labels: [8-001B, 8-001B]
        to: {field_interface: signal}
      supply:
        from: {terminal: X3-001}
        via: [X8-001A]
        labels: [38-001, 8-001A]
        to: {field_interface: supply}
      common_ground:
        from: {point: UI1, interface: common}
        via: []
        labels: [X8-002]
        to: {terminal: X8-002A}
      common_ground_mk2:
        from: {terminal: X4-001}
        via: []
        labels: [48-001]
        to: {terminal: X8-002B}
```

| Row | Controller | Route | Drawn as |
| --- | --- | --- | --- |
| 1 | UI1 / 1 | `signal` | UI1 → X8-001B → SIG |
| 2 | C 1/2 / 2 | `common_ground` | C 1/2 → X8-002A |
| 3 |  | `supply` | X3-001 → X8-001A, bends up into + |
| 4 |  | `common_ground_mk2` | X4-001 → X8-002B |

`supply` would like row 2 (the + terminal), but the common already uses it, so it moves to row 3 and bends 12 px before the device. A second sensor on UI2 reuses the C 1/2 terminal and needs only its `signal` and `supply` routes.
