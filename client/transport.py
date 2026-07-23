"""LXMF transport layer for client–coordinator communication.

Wraps LXMF to provide reliable message delivery to the coordinator
and handles incoming messages (state updates, phase notifications).
"""

# TODO: Implement transport
#   - connect(coordinator_address)
#   - send_message(msg) -> delivery status
#   - register_handler(msg_type, callback)
#   - Handle delivery receipts and retries
