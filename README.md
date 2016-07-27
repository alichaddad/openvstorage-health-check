# Health check for Open vStorage, Alba & Arakoon

## Description

The health check is classified as a monitoring, detection and healing tool for Open vStorage `Eugene-updates`.

**Note:** You will have to deploy this on every Open vStorage node.

## Pulling this repository
```
sudo apt-get install -y git
git clone -b eugene-updates https://github.com/openvstorage/openvstorage-health-check.git
```

## Installation (AUTOMATIC)

### Deploying by script
```
cd openvstorage-health-check; bash bin/post-install.sh
```

## Installation (MANUAL)
### Required packages for Health Check (eugene-updates)
```
wget https://bootstrap.pypa.io/get-pip.py; python get-pip.py
pip install flower
pip install psutil
pip install xmltodict
```

### Add the Open vStorage healtcheck to the required directory
```
cd openvstorage-health-check; mkdir -p /opt/OpenvStorage-healthcheck; cp -r * /opt/OpenvStorage-healthcheck
```

### Add following code to Health Check Open vStorage commands

```
vim /usr/bin/ovs
```

```
elif [ "$1" = "healthcheck" ] ; then
    cd /opt/OpenvStorage-healthcheck
    if [ "$2" = "unattended" ] ; then
        # launch unattended healthcheck
        python -c "from ovs_health_check.main import Main; Main(True)"
    else
        # launch healthcheck
        python ovs_health_check/main.py
    fi
```

### Execution by hand

```
# via Open vStorage commands
ovs healthcheck

# native python execution
cd /opt/OpenvStorage-healthcheck/

python ovs_health_check/main.py
```

## Monitoring with CheckMK or other server-deamon monitoring systems

### OUTPUT for CheckMK or other monitoring systems

```
ovs healthcheck unattended
```

### Execute by CRON.hourly *(will only generate logs)*

```
* *   * * *  root  /usr/bin/ovs healthcheck unattended
```

# Important to know!
* No files in the vPools may be named after: `ovs-healthcheck-test-{storagerouter_id}.xml`
* No volumes in the vPools may be named after: `ovs-healthcheck-test-{storagerouter_id}.raw`

## Branch Info or contributions
* The 'master' branch is marked as the main but unstable branch
* The 'release' branches are the official releases of the HEALTH CHECK Project
* We'd love to have your contributions, read [Community Information](CONTRIBUTION.md) and [Rules of conduct](RULES.md) for notes on how to get started.

## File a bug
Open vStorage and its automation is quality checked to the highest level.
Unfortunately we might have overlooked some tiny topics here or there.
The Open vStorage HEALTH CHECK Project maintains a [public issue tracker](https://github.com/openvstorage/openvstorage-health-check/issues)
where you can report bugs and request features.
This issue tracker is not a customer support forum but an error, flaw, failure, or fault in the Open vStorage software.

If you want to submit a bug, please read the [Community Information](CONTRIBUTION.md) for notes on how to get started.

# License
The Open vStorage HealthCheck is licensed under the [GNU AFFERO GENERAL PUBLIC LICENSE Version 3](https://www.gnu.org/licenses/agpl.html).
