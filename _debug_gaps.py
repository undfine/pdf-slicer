import fitz

doc   = fitz.open("test.pdf")
page  = doc[0]
blocks = page.get_text("rawdict", flags=0)["blocks"]

found_tracked = False
found_body    = False

for b in blocks:
    if b.get("type") != 0:
        continue
    for line in b.get("lines", []):
        for span in line.get("spans", []):
            chars = span.get("chars", [])
            font  = span.get("font", "")
            sz    = span.get("size", 0)
            text  = "".join(ch.get("c", "") for ch in chars)

            if not found_tracked and "FieldGothic" in font and len(chars) > 10:
                found_tracked = True
                gaps = [round(chars[i+1]["bbox"][0] - chars[i]["bbox"][2], 2) for i in range(min(20, len(chars)-1))]
                print("TRACKED  font:", font, "size:", round(sz, 1))
                print("  chars text:", repr(text[:60]))
                print("  gaps[0:20]:", gaps)
                print()

            if not found_body and "DMSans" in font and len(chars) > 30:
                found_body = True
                gaps = [round(chars[i+1]["bbox"][0] - chars[i]["bbox"][2], 2) for i in range(min(30, len(chars)-1))]
                print("BODY     font:", font)
                print("  chars text:", repr(text[:60]))
                print("  gaps[0:30]:", gaps)

            if found_tracked and found_body:
                break
        if found_tracked and found_body:
            break
    if found_tracked and found_body:
        break
