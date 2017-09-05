# About
This charm installs a Serial Vault service, https://github.com/CanonicalLtd/serial-vault

# Install
After bootstrapping a juju environment, run:
```bash
juju deploy postgresql

juju deploy cs:~canonical-solutions/serial-vault-charm serial-vault         # The signing service
juju add-relation serial-vault:database postgresql:db-admin

juju deploy cs:~canonical-solutions/serial-vault-charm serial-vault-admin   # The admin service
juju add-relation serial-vault-admin:database postgresql:db-admin
juju config serial-vault-admin service_type=admin

# Optionally, deploy the system-user service (v1.5 snap onwards)
juju deploy cs:~canonical-solutions/serial-vault-charm serial-vault-user   # The system-user service
juju add-relation serial-vault-user:database postgresql:db-admin
juju config serial-vault-user service_type=system-user

# Expose the services
juju expose serial-vault         # port 8080
juju expose serial-vault-admin   # port 8081
juju expose serial-vault-user    # port 8082
```

Note: the db-admin relation is needed for the PostgreSQL service currently to avoid object ownership issues.
