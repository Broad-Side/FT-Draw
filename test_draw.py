import copy
import xml.etree.ElementTree as ET


CONTROLLER_FILE = "drawioTemplates/controllers/SNC25152.drawio"
DEVICE_FILE = "drawioTemplates/devices/AI_10K.drawio"
OUTPUT_FILE = "output.drawio"


def load_template(path):
    tree = ET.parse(path)
    root = tree.getroot()

    graph_root = root.find(".//root")

    if graph_root is None:
        raise ValueError(f"Could not find graph root in {path}")

    return tree, graph_root


def find_object(graph_root, object_id):

    for obj in graph_root.iter("object"):

        if obj.attrib.get("id") == object_id:

            cell = obj.find("mxCell")
            geom = cell.find("mxGeometry")

            return {
                "object": obj,
                "cell": cell,
                "geometry": geom,
                "x": float(geom.attrib["x"]),
                "y": float(geom.attrib["y"]),
            }

    raise ValueError(
        f"Could not find object: {object_id}"
    )


#
# Load controller
#

controller_tree, controller_root = load_template(
    CONTROLLER_FILE
)

ui1 = find_object(
    controller_root,
    "UI1"
)

#
# Load device
#

device_tree, device_root = load_template(
    DEVICE_FILE
)

signal = find_object(
    device_root,
    "signal"
)

#
# Copy device objects into controller drawing
#

for child in list(device_root):

    if child.attrib.get("id") in ["0", "1"]:
        continue

    new_child = copy.deepcopy(child)

    #
    # Move device left of controller
    #

    geom = new_child.find(".//mxGeometry")

    if geom is not None:

        x = float(geom.attrib.get("x", "0"))
        y = float(geom.attrib.get("y", "0"))

        geom.attrib["x"] = str(x - 300)

        geom.attrib["y"] = str(y)

    controller_root.append(
        new_child
    )

#
# Create connector
#

connector = ET.Element(
    "mxCell",
    {
        "id": "edge1",
        "edge": "1",
        "parent": "1",
        "source": "signal",
        "target": "UI1",
        "style":
            "endArrow=none;"
            "html=1;"
            "strokeWidth=2;"
    }
)

geometry = ET.SubElement(
    connector,
    "mxGeometry",
    {
        "relative": "1",
        "as": "geometry"
    }
)

controller_root.append(
    connector
)

#
# Save
#

controller_tree.write(
    OUTPUT_FILE,
    encoding="utf-8",
    xml_declaration=True
)

print(
    f"Created {OUTPUT_FILE}"
)
