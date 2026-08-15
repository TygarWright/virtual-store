from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
admin=(ROOT/'blueprints/admin.py').read_text()
comm=(ROOT/'permissions_comm.py').read_text()
k=(ROOT/'backend_kernel.py').read_text()
db=(ROOT/'database.py').read_text()
contract=(ROOT/'schema_contract.py').read_text()
api=(ROOT/'admin_api.py').read_text()
assert 'def reply_to_message' in comm
assert 'def toggle_reaction' in comm
assert 'team_message_reactions' in db
assert 'domain_event_deliveries' in db
assert 'def list_domain_events' in k and 'def record_event_delivery' in k
assert 'team.message.created' in comm
assert '/team-hub/reply' in admin and '/team-hub/reaction' in admin
assert '/governance/event-deliveries' in api
assert 'domain_event_deliveries' in contract and 'parent_message_id' in contract
print('ROUND42_PD_PASS')
