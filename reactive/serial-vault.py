import os
import tempfile
import shutil

from subprocess import (
    check_call,
    check_output
)
from charmhelpers.core.hookenv import (
    charm_dir, 
    local_unit, 
    log, 
    relation_get, 
    relation_id, 
    relation_set, 
    related_units)
from charmhelpers.core import (
    templating,
    hookenv,
    host
)
from charmhelpers.fetch import (
    apt_install,
    install_remote
)
from charms.reactive import (
    hook, 
    is_state, 
    set_state
)

PORTS = {
    'admin': {'open': 8081, 'close': [8080, 8082]},
    'signing': {'open': 8080, 'close': [8081, 8082]},
    'system-user': {'open': 8082, 'close': [8080, 8081]},
}

PROJECT = 'serial-vault'
SERVICE = '{}.service'.format(PROJECT)
AVAILABLE = '{}.available'.format(PROJECT)
ACTIVE = '{}.active'.format(PROJECT)

SYSTEMD_UNIT_FILE = os.path.join(charm_dir(), 'files', 'systemd', SERVICE)

DATABASE_NAME = 'serialvault'

BINDIR = '/usr/bin'
LIBDIR = '/usr/lib/{}'.format(PROJECT)
CONFDIR = '/etc/{}'.format(PROJECT)
ASSETSDIR = '/usr/share/{}'.format(PROJECT)

@hook("install")
def install():
    """Charm install hook
    Fetches the Serial Vault service payload and installs it. 
    Configuration cannot be done until the database is available.
    """
    if is_state(AVAILABLE):
        return

    # Open the relevant port for the service
    open_port()

    # Deploy binaries and systemd configuration, but it won't be ready until it has a db connection
    download_and_deploy_service()

    # Don't start until having db connection
    enable_service()

    hookenv.status_set('maintenance', 'Waiting for database')
    set_state(AVAILABLE)


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

    # Refresh the service payload and restart the service
    refresh_service()

    hookenv.status_set('active', '')
    set_state(ACTIVE)


@hook('database-relation-joined')
def db_relation_joined(*args):
    # Use a specific database name
    relation_set(database=DATABASE_NAME)


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


@hook('upgrade-charm')
def upgrade_charm():
    refresh_service()


def refresh_service():
    hookenv.status_set('maintenance', 'Refresh the service')

    # Overrides previous deployment
    download_and_deploy_service()

    restart_service()

    hookenv.status_set('active', '')
    set_state(ACTIVE)


def configure_service():
    """Create service config file and place it in /usr/local/etc.
    Get the database settings and create the service config file
    """

    hookenv.status_set('maintenance', 'Configure the service')

    # Open the relevant port for the service
    open_port()

    database = get_database()
    if not database:
        return

    update_config(database)


def update_config(database):
    # Create the configuration file for the service in CONFDIR path
    create_settings(database)

    # Restart the service
    restart_service()

    hookenv.status_set('active', '')
    set_state(ACTIVE)


def get_database():
    if not relation_get('database'):
        log("Database not ready yet... skipping it for now")
        return None

    database = None
    for db_unit in related_units():
        # Make sure that we have the specific database for the serial vault
        if relation_get('database', db_unit) != DATABASE_NAME:
            continue

        remote_state = relation_get('state', db_unit)
        if remote_state in ('master', 'standalone'):
            database = relation_get(unit=db_unit)

    if not database:
        log("Database not ready yet... skipping it for now")
        hookenv.status_set('maintenance', 'Waiting for database')
        return None

    return database


def download_and_deploy_service():
    """ Downloads from swift container and deploys service payload
    """
    payload_local_path = download_service_payload_from_swift_container()
    
    # In case an empty path is returned, search for payload settings value
    # and treat it as a direct downloadable payload url
    if not payload_local_path:
        config = hookenv.config()
        payload_local_path = config['payload']

    deploy_service_payload(payload_local_path)


def download_service_payload_from_swift_container():
    """ Updates environment with 'environment_variables' defined ones,
    gets container and payload references from config, and use them
    to download from swift the service payload.
    Method returns the path to the downloaded file
    """
    hookenv.status_set('maintenance', 'Download service payload from swift container')
    
    # Update environment with vars defined in 'environment_variables' config
    update_env()

    config = hookenv.config()
    container = config['swift_container']
    payload = config['payload']
    if not container or not payload:
        return ''

    apt_install('python-swiftclient')
    check_call(['swift', '-v', 
        '--os-username', os.environ.get('OS_USERNAME'),
        '--os-tenant-name', os.environ.get('OS_TENANT_NAME'),
        '--os-password', os.environ.get('OS_PASSWORD'),
        '--os-auth-url', os.environ.get('OS_AUTH_URL'),
        '--os-region-name', os.environ.get('OS_REGION_NAME'),
        'download',
        container,
        payload])

    hookenv.status_set('maintenance', 'Service payload downloaded')

    # payload would be deployed to current folder
    return 'file://{}'.format(os.path.join(charm_dir(), payload));


def deploy_service_payload(payload_path):
    """ Gets serial vault payload, uncompresses it in a
    temporary folder and:
    - moves serial-vault and serial-vault-admin to /usr/lib/serial-vault
    - moves static assets to /usr/share/serial-vault
    - moves serial-vault.service to /etc/systemd/system
    - creates settings and store in /etc/serial-vault/settings.yaml
    - creates launchers and stores them in /usr/bin which will use the ones in /usr/lib/serial-vault
    """
    hookenv.status_set('maintenance', 'Deploy service payload')

    # In case there is no payload path, read it from config payload setting
    if not payload_path:
        config = hookenv.config()
        payload_path = config['payload']
        if not payload_path:
            raise Exception('payload not available')
    
    tmp_dir = tempfile.mkdtemp()
    payload_dir = install_remote(payload_path, dest=tmp_dir)
    if payload_dir == tmp_dir:
        log('Got binaries tgz at {}'.format(payload_dir))
        
        if not os.path.isfile(os.path.join(payload_dir, 'serial-vault')):
            log('Could not find serial-vault binary')
            return
        if not os.path.isfile(os.path.join(payload_dir, 'serial-vault-admin')):
            log('Could not find serial-vault-admin binary')
            return
        if not os.path.isdir(os.path.join(payload_dir, 'static')):
            log('Could not find static assets')
            return
    
        # In case this is updating assets, remove old ones folder.
        if os.path.exists(ASSETSDIR):
            shutil.rmtree(ASSETSDIR)
        os.mkdir(ASSETSDIR, mode=755)
        
        if not os.path.exists(CONFDIR):
            os.mkdir(CONFDIR, mode=755)
        if not os.path.exists(LIBDIR):
            os.mkdir(LIBDIR, mode=755)

        shutil.move(os.path.join(payload_dir, 'serial-vault'), LIBDIR)
        shutil.move(os.path.join(payload_dir, 'serial-vault-admin'), LIBDIR)
        shutil.move(os.path.join(payload_dir, 'static'), ASSETSDIR)
        shutil.copy(SYSTEMD_UNIT_FILE, '/etc/systemd/system/')
        create_launchers()

        # Reload daemon, as systemd service task file has been overriden
        reload_systemd()

    hookenv.status_set('maintenance', 'Service payload deployed')


def create_settings(postgres):
    hookenv.status_set('maintenance', 'Configuring service')
    config = hookenv.config()
    settings_path = '{}/{}'.format(CONFDIR, 'settings.yaml') 
    templating.render(
        source='settings.yaml',
        target=settings_path,
        context={
            'docRoot': ASSETSDIR,
            'keystore_secret': config['keystore_secret'],
            'service_type': config['service_type'],
            'csrf_auth_key': config['csrf_auth_key'],
            'db': postgres,
            'url_host': config['url_host'],
            'enable_user_auth': bool(config['enable_user_auth']),
        }
    )
    os.chmod(settings_path, 755)


def create_launchers():
    # bindir context var is assigned to LIBDIR because is where binaries will be stored.
    # Launchers will be stored instead in /usr/bin pointing to these LIBDIR binaries
    sv_admin_path = '{}/{}'.format(BINDIR, 'serial-vault-admin')
    templating.render(
        source='serial-vault-admin-launcher.sh',
        target=sv_admin_path,
        context={
            'bindir': LIBDIR,
            'confdir': CONFDIR,
        }
    )

    sv_path = '{}/{}'.format(BINDIR, 'serial-vault')
    templating.render(
        source='serial-vault-launcher.sh',
        target=sv_path,
        context={
            'bindir': LIBDIR,
            'confdir': CONFDIR,
        }
    )

    os.chmod(sv_admin_path, 755)
    os.chmod(sv_path, 755)


def open_port():
    """
    Open the port that is requested for the service and close the others.
    """
    config = hookenv.config()
    port_config = PORTS.get(config['service_type'])
    if port_config:
        hookenv.open_port(port_config['open'], protocol='TCP')
        for port in port_config['close']:
            hookenv.close_port(port, protocol='TCP')


def enable_service():
    host.service('enable', SERVICE)


def restart_service():
    host.service_restart(SERVICE)


def reload_systemd():
    host.service_reload('daemon-reload')


def update_env():
    config = hookenv.config()
    env_vars_string = config['environment_variables']

    if env_vars_string:
        for env_var_string in env_vars_string.split(' '):
            key, value = env_var_string.split('=')
            value = dequote(value)
            log('setting env var {}={}'.format(key, value))
            os.environ[key] = value


def dequote(s):
    """
    If a string has single or double quotes around it, remove them.
    If a matching pair of quotes is not found, return the string unchanged.
    """

    if (
        s.startswith(("'", '"')) and s.endswith(("'", '"'))
        and (s[0] == s[-1])  # make sure the pair of quotes match
    ):
        s = s[1:-1]
    return s
