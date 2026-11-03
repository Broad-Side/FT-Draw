import copy
import xml.etree.ElementTree as ET

CONTROLLER_FILE = "drawioTemplates/controllers/SNC25152.drawio"
DEVICE_FILE = "drawioTemplates/devices/AI_10K.drawio"

OUTPUT_FILE = "output.drawio"


def load_drawio(path):

    tree = ET.parse(path)

    root = tree.getroot()

    graph_root = root.find(".//root")

    if graph_root is None:
        raise Exception(f"No graph root in {path}")

    return tree, graph_root


def find_object(graph_root, object_id):

    for obj in graph_root.iter("object"):

        if obj.attrib.get("id") == object_id:

            cell = obj.find("mxCell")

            if cell is None:
                raise Exception(
                    f"No mxCell for {object_id}"
                )

            geometry = cell.find("mxGeometry")

            if geometry is None:
                raise Exception(
                    f"No geometry for {object_id}"
                )

            return {
                "object": obj,
                "cell": cell,
                "geometry": geometry,
                "x": float(
                    geometry.attrib.get("x", 0)
                ),
                "y": float(
                    geometry.attrib.get("y", 0)
                ),
            }

    raise Exception(
        f"Cannot find object '{object_id}'"
    )


#
# Load controller template
#

controller_tree, controller_root = load_drawio(
    CONTROLLER_FILE
)

#
# Verify controller points exist
#

ui1 = find_object(
    controller_root,
    "UI1"
)

ui1_common = find_object(
    controller_root,
    "UI1_COMMON"
)

print("Controller Points")
print("-----------------")
print(
    f"UI1         ({ui1['x']},{ui1['y']})"
)
print(
    f"UI1_COMMON  "
    f"({ui1_common['x']},{ui1_common['y']})"
)

#
# Load device template
#

device_tree, device_root = load_drawio(
    DEVICE_FILE
)

#
# Copy device objects into controller drawing
#

offset_x = -300
offset_y = 0

for child in list(device_root):

    if child.attrib.get("id") in ["0", "1"]:
        continue

    cloned = copy.deepcopy(child)

    geom = cloned.find(".//mxGeometry")

    if geom is not None:

        x = float(
            geom.attrib.get("x", 0)
        )

        y = float(
            geom.attrib.get("y", 0)
        )

        geom.attrib["x"] = str(
            x + offset_x
        )

        geom.attrib["y"] = str(
            y + offset_y
        )

    controller_root.append(
        cloned
    )

#
# Verify device interfaces exist
#

signal = find_object(
    controller_root,
    "signal"
)

common = find_object(
    controller_root,
    "common"
)

print()
print("Device Interfaces")
print("-----------------")
print(
    f"signal  ({signal['x']},{signal['y']})"
)
print(
    f"common  ({common['x']},{common['y']})"
)

#
# Signal Wire
#

edge_signal = ET.Element(
    "mxCell",
    {
        "id": "edge_signal",
        "edge": "1",
        "parent": "1",
        "source": "signal",
        "target": "UI1",
        "style": (
            "endArrow=none;"
            "html=1;"
            "strokeWidth=2;"
        ),
        "value": "SIG"
    }
)

ET.SubElement(
    edge_signal,
    "mxGeometry",
    {
        "relative": "1",
        "as": "geometry"
    }
)

controller_root.append(
    edge_signal
)

#
# Common Wire
#

edge_common = ET.Element(
    "mxCell",
    {
        "id": "edge_common",
        "edge": "1",
        "parent": "1",
        "source": "common",
        "target": "UI1_COMMON",
        "style": (
            "endArrow=none;"
            "html=1;"
            "strokeWidth=2;"
        ),
        "value": "COM"
    }
)

ET.SubElement(
    edge_common,
    "mxGeometry",
    {
        "relative": "1",
        "as": "geometry"
    }
)

controller_root.append(
    edge_common
)

#
# Save drawing
#

controller_tree.write(
    OUTPUT_FILE,
    encoding="utf-8",
    xml_declaration=True
)

print()
print("Drawing Generated")
print("-----------------")
print(f"Output File : {OUTPUT_FILE}")
print()
print("Connections")
print("-----------")
print("signal -> UI1")
print("common -> UI1_COMMON")