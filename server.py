
# Import start_listener from listener.py
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
    """
    if message.get("type") != "GET":
        return []

    path = message.get("path")
    if not path:
        return []

    try:
        fragments = fragment_html_file(f"html{path}")
        return create_response_envelopes(path, fragments)
    except FileNotFoundError:
        print(f"File not found: {path}")
        return []


# Start the MiniHTTP server by listening for GET messages and responding.
def start_server(radio):
    """
    Starts the MiniHTTP server by listening for GET messages and responding.
    """
    def handle_message(message):
        responses = handle_get_message(message)
        for resp in responses:
            radio.sendText(json.dumps(resp))

    start_listener(radio, handle_message)