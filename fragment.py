
def fragment_html_file(filepath):
    """
    Reads an HTML file and returns a list of 122-byte fragments.
    """
    fragments = []
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    for i in range(0, len(content), 122):
        chunk = content[i:i+122]
        fragments.append(chunk)

    return fragments