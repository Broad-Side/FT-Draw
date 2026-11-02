import xml.etree.ElementTree as ET


def find_object(drawio_file, object_id):
    tree = ET.parse(drawio_file)
    root = tree.getroot()

    for obj in root.iter("object"):

        if obj.attrib.get("id") == object_id:

            cell = obj.find("mxCell")

            if cell is None:
                raise ValueError(
                    f"No mxCell found for {object_id}"
                )

            geometry = cell.find("mxGeometry")

            if geometry is None:
                raise ValueError(
                    f"No geometry found for {object_id}"
                )

            return {
                "id": object_id,
                "x": float(geometry.attrib.get("x", 0)),
                "y": float(geometry.attrib.get("y", 0)),
            }

    raise ValueError(
        f"Object '{object_id}' not found in {drawio_file}"
    )


controller = find_object(
    "drawioTemplates/controllers/SNC25152.drawio",
    "UI1"
)

device = find_object(
    "drawioTemplates/devices/AI_10K.drawio",
    "signal"
)

print("\nController Point")
print("----------------")
print(controller)

print("\nDevice Interface")
print("----------------")
print(device)

print("\nProposed Connection")
print("-------------------")

print(
    f"{device['id']} ({device['x']},{device['y']})"
)

print("        |")

print("        |")

print(
    f"{controller['id']} ({controller['x']},{controller['y']})"
)