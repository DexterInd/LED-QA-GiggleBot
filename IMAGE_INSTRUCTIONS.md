## How To Use the Image

The use of a flash drive is mandatory for this image. The process goes like this:

1. Wait for the Raspberry Pi to fully boot up. Preferably you would want it connected to the monitor.

1. Plug in a FAT32 flash drive into any USB port.

1. Wait a few seconds until the antenna LED of the GPG3 turns to <span style="color:green">green</span>.

1. Do QA tests on as many GBs as you want.

1. Power down the RaspberryPi by pressing on the power button of the GoPiGo3.

The **recommended way** of taking out the flash drive from the Raspberry Pi is to **press the power button on the GoPiGo3** and wait it to fully shutdown. 

You can also remove the flash drive during its operation and have the application stop accordingly, but it's not a safe thing to do - data may get corrupted on the flash drive. Hot plugging it back in will start the app again.

## Creating the Image

For this project to be ready for use in China, we need to set up an image for Jin. The instructions for this image are the following.

#### Docker & Devmon

Download the latest Raspian Desktop image (as of this moment it's the stretch distribution) and proceed with the following.

Install docker with the following command:
```bash
curl -fsSL get.docker.com -o get-docker.sh && sh get-docker.sh
```
And then add `pi` user to the `docker` group.
```bash
sudo usermod -aG docker pi
newgrp docker
```

Download the image for the GB QA app with:
```bash
docker image pull robertlucian/gbtest
```

For our next installation, we need to get `devmon` installed. Run this:
```bash
sudo apt-get update
sudo apt-get install devmon -y
```

Next, follow the instructions in the [README.md](README.md) to enable the required devices (PiCamera, SPI) and to change the split size for the GPU.

Then create a file in `/opt/gbqa/start_devmon.sh` with the following contents:
```bash
#!/usr/bin/env bash

devmon --exec-on-drive "docker container run --rm -d --name gbtest --privileged -v %d:/app/data -v /run/lock:/run/lock robertlucian/gbtest" \
        --exec-on-unmount "docker container kill --signal=SIGINT gbtest && ! timeout 3 docker container wait gbtest && docker container stop gbtest" \
        --exec-on-remove  "docker container kill --signal=SIGINT gbtest && ! timeout 3 docker container wait gbtest && docker container stop gbtest"
```

Create a systemd service in `/etc/systemd/system/gbqa.service` with these contents:
```
[Unit]
Description=Devmon App To Launch GB-QA Application On Drive

[Service]
Type=idle
ExecStart=/bin/bash /opt/gbqa/start_devmon.sh

[Install]
WantedBy=multi-user.target
```

To enable the service and load it do:
```bash
sudo systemctl daemon-reload
sudo systemctl enable gbqa
```

#### GPG3 Power Button

Next, let's setup the power button service for the GPG3.
Create a script called `gpg3_power.py` in `/opt/gbqa` and add these lines:
```python
import RPi.GPIO as GPIO
import time
import os

GPIO.setmode(GPIO.BCM)
GPIO.setup(22, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

GPIO.setup(23, GPIO.OUT)
GPIO.output(23, True)

while True:
    if GPIO.input(22):
        os.system('docker container kill --signal=SIGINT gbtest')
        os.system('timeout 3 docker container wait gbtest')
        os.system('docker container stop gbtest')
        os.system('shutdown now -h')
    time.sleep(0.1)
```
Next, let's install the `RPi.GPIO` library:
```bash
sudo pip install RPi.GPIO
```
Wait for it to finish and then add a service for the above script. Create one in `/etc/systemd/system/gpg3_power.service`:
```bash
[Unit]
Description=GoPiGo3 Power Service

[Service]
Type=idle
ExecStart=/usr/bin/env python  /opt/gbqa/gpg3_power.py

[Install]
WantedBy=multi-user.target
```

And finally just reload the daemon and enable the service.
```bash
sudo systemctl daemon-reload
sudo systemctl enable gpg3_power
```

#### Disabling Auto-Mounting Process

In order to let the new mounter to automatically mount new flash drives, we gotta disable the built-in one that comes with Raspbian Desktop.

Go to _File Manager > Edit > Preferences > Volume Management_ from the GUI interface (the Desktop) and uncheck all 3 auto-mounting options. 

#### Finalizing

Reboot and give it a shot with a flash drive and then finally rip the image and send it to Jin.