from subprocess import call
from subprocess import check_output

from charmhelpers.core import hookenv
from charmhelpers.core.hookenv import (
    local_unit, log, relation_get, relation_id, relation_set, related_units)
from charmhelpers.core import templating
from charms.reactive import hook
from charms.reactive import is_state
from charms.reactive import set_state


PORTS = {
    'admin': {'open': 8081, 'close': 8080},
    'signing': {'open': 8080, 'close': 8081},
}


@hook('install')
def install():
    """Charm install hook

    Fetches the Serial Vault snap and installs it. Configuration cannot
    be done until the database is available.
    """
    if is_state('serial-vault.available'):
        return

    # Open the relevant port for the service
    open_port()

    # Set the proxy server and restart the snapd service, if required
    set_proxy_server()

    # Install the snap, but it won't be ready until it has a db connection
    install_snap()

    hookenv.status_set('maintenance', 'Waiting for database')
    set_state('serial-vault.available')


@hook('config-changed')
def config_changed():
    rel_ids = list(hookenv.relation_ids('database'))
    if len(rel_ids) == 0:
        log("Database not ready yet... skipping it for now")
        return

    # Get the database settings
    db_id = rel_ids[0]
    relations = hookenv.relations()['database'][db_id]
    database = None
    for key, value in relations.items():
        if key.startswith('postgresql'):
            database = value
    if not database:
        log("Database not ready yet... skipping it for now")
        return

    # Open the relevant port for the service
    open_port()

    # Update the config file with the service_type and database settings
    update_config(database)

    # Restart the snap
    restart_service('snap.serial-vault.serial-vault.service')

    hookenv.status_set('active', '')
    set_state('serial-vault.active')


@hook('database-relation-changed')
def db_relation_changed(*args):
    configure_service()


@hook('website-relation-changed')
def website_relation_changed(*args):
    """
    Set the hostname and the port for reverse proxy relations
    """
    config = hookenv.config()
    port_config = PORTS.get(config['service_type'])
    if port_config:
        port = port_config['open']
    else:
        port = PORTS['signing']['open']

    relation_set(
        relation_id(), {'port': port, 'hostname': local_unit().split('/')[0]})


def configure_service():
    """Create snap config file and send it to the snap

    Get the database settings and create the service config file. Pipe it to
    the service using the config command. This will overwrite the settings on
    the snap's filesystem.
    """

    hookenv.status_set('maintenance', 'Configure the service')

    # Open the relevant port for the service
    open_port()

    database = get_database()
    if not database:
        return

    update_config(database)


def update_config(database):
    # Create the configuration file for the snap
    create_settings(database)

    # Send the configuration file to the snap
    check_output(
        'cat settings.yaml | sudo /snap/bin/serial-vault.config', shell=True)

    # Restart the snap
    restart_service('snap.serial-vault.serial-vault.service')

    hookenv.status_set('active', '')
    set_state('serial-vault.active')


def get_database():
    if not relation_get('database'):
        log("Database not ready yet... skipping it for now")
        return None

    database = None
    for db_unit in related_units():
        remote_state = relation_get('state', db_unit)
        if remote_state in ('master', 'standalone'):
            database = relation_get(unit=db_unit)

    if not database:
        log("Database not ready yet... skipping it for now")
        hookenv.status_set('maintenance', 'Waiting for database')
        return None

    return database


def set_proxy_server():
    """Set up the proxy server for snapd.

    Some environments may need a proxy server to access the Snap Store. The
    access is from snapd rather than the snap command, so the system-wide
    environment file needs to be updated and snapd needs to be restarted.
    """
    config = hookenv.config()
    if len(config['proxy']) == 0:
        return

    # Update the /etc/environment file
    env_command = 'echo "{}={}" | sudo tee -a /etc/environment'
    check_output(
        env_command.format('http_proxy', config['proxy']), shell=True)
    check_output(
        env_command.format('https_proxy', config['proxy']), shell=True)

    # Restart the snapd service
    restart_service('snapd')


def install_snap():
    hookenv.status_set('maintenance', 'Install snap')

    # Fetch the snap from the store and install it
    call(['sudo', 'snap', 'install', 'serial-vault'])

    hookenv.status_set('maintenance', 'Installed snap')


def create_settings(postgres):
    hookenv.status_set('maintenance', 'Configuring service')
    config = hookenv.config()
    templating.render(
        source='settings.yaml',
        target='settings.yaml',
        context={
            'keystore_secret': config['keystore_secret'],
            'service_type': config['service_type'],
            'db': postgres,
        }
    )


def open_port():
    config = hookenv.config()
    port_config = PORTS.get(config['service_type'])
    if port_config:
        hookenv.open_port(port_config['open'], protocol='TCP')
        hookenv.close_port(port_config['close'], protocol='TCP')


def restart_service(service):
    call(['sudo', 'systemctl', 'restart', service])
