

# server.py: MiniHTTP server entry point
from listener import start_listener
import json

def create_response_envelopes(path, fragments):
    """
    Given a file path and a list of fragments, returns a list of response envelopes
    conforming to the MiniHTTP spec.
    """
    total = len(fragments)
    envelopes = []

    for i, frag in enumerate(fragments):
        envelopes.append({
            "type": "RESP",
            "path": path,
            "frag": i + 1,
            "of_frag": total,
            "data": frag
        })

    return envelopes

from fragment import fragment_html_file


def handle_get_message(message):
    """
    Processes a GET message and returns a list of response envelopes.
    Handles full file or single fragment requests.
    """
    if message.get("type") != "GET":
        return []

    path = message.get("path")
    if not path:
        return []

    try:
        fragments = fragment_html_file(f"html{path}")
        frag_num = message.get("frag")
        if frag_num is not None:
            # Return only the requested fragment (frag is 1-based)
            frag_num = int(frag_num)
            if 1 <= frag_num <= len(fragments):
                return [create_response_envelopes(path, fragments)[frag_num-1]]
            else:
                print(f"Requested fragment {frag_num} out of range for {path}")
                return []
        return create_response_envelopes(path, fragments)
    except FileNotFoundError:
        print(f"File not found: {path}")
        return []



def start_server(radio):
    """
    Starts the MiniHTTP server by listening for GET messages and responding.
    """
    def handle_message(message):
        responses = handle_get_message(message)
        for resp in responses:
            radio.sendText(json.dumps(resp))

    start_listener(radio, handle_message)


# Only run the server if this script is executed directly
if __name__ == "__main__":
    try:
        from radio import RadioInterface
        import time
        radio = RadioInterface()
        print("Starting MiniHTTP server...")
        start_server(radio.iface)
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"Failed to start server: {e}")