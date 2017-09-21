#!/bin/sh

{{ bindir }}/serial-vault-admin database --config={{ confdir }}/settings.yaml

{{ bindir }}/serial-vault -config={{ confdir }}/settings.yaml
