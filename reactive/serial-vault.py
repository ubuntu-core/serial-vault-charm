import os
import logging
from subprocess import call, check_output, PIPE, Popen

from charms.reactive import when, hook
from charms.reactive import is_state, set_state, remove_state
from charmhelpers import fetch
from charmhelpers.core import hookenv
from charmhelpers.core import templating
from charmhelpers.core.hookenv import (
    config, local_unit, log, relation_get, related_units)


PORTS = {
    'admin': 8081,
    'signing': 8080
}


@hook('install')
def install():
    """
    Fetches the Serial Vault snap and installs it. Configuration cannot
    be done until the database is available.
    """
    if is_state('serial-vault.available'):
        return

    # Open the relevant port for the service
    open_port()

    # Install the sanap, but it won't be ready until it has a db connection
    install_snap()

    hookenv.status_set('maintenance', 'Waiting for database')
    set_state('serial-vault.available')


@hook('config-changed')
def config_changed():
    configure_service()


@hook('database-relation-changed')
def db_relation_changed(*args):
    configure_service()


def configure_service():
    """
    Get the database settings and create the service config file. Pipe it to
    the service using the config command. This will overwrite the settings on
    the snap's filesystem.
    """
    hookenv.status_set('maintenance', 'Configure the service')

    database = get_database()
    if not database:
        return

    # Create the configuration file for the snap
    create_settings(database)

    # Send the configuration file to the snap
    ps = Popen(['cat', 'settings.yaml'], stdout=PIPE)
    check_output([
        'sudo', '/snap/bin/serial-vault.config'], stdin=ps.stdout)
    ps.wait()

    # Restart the snap
    call([
        'sudo', 'systemctl', 'restart',
        'snap.serial-vault.serial-vault.service'])

    hookenv.status_set('active', '')
    set_state('serial-vault.active')


def get_database():

    if not relation_get('database'):
        log("Database not ready yet... skipping it for now")
        hookenv.status_set('maintenance', 'Waiting for database')
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
    if PORTS.get(config['service_type']):
        hookenv.open_port(PORTS[config['service_type']], protocol='TCP')
