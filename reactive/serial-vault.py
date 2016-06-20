from charms.reactive import when, when_not
from charms.reactive import set_state, remove_state


@when('database.available')
def setup_serial_vault(postgres):
    print("---setup_serial_vault")
    print(postgres)
    set_state('serial_vault.start')
    pass
