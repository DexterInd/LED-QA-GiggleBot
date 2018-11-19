FROM python:3.5-stretch

WORKDIR /build

RUN apt-get update -qq && \
     apt-get install git -y --no-install-recommends

RUN git clone https://github.com/DexterInd/GoPiGo3 && \
    git clone https://github.com/DexterInd/RFR_Tools

WORKDIR /build/GoPiGo3
RUN git reset --hard 5849047558aa1e573b49dc572adec44d9960084c
WORKDIR /build/RFR_Tools
RUN git reset --hard 2ec15465d8d69bffdea5314d5fd86812eaf68085

WORKDIR /build
RUN pip wheel GoPiGo3/Software/Python --wheel-dir=wheels && \
    pip wheel RFR_Tools/miscellaneous --wheel-dir=wheels

FROM resin/rpi-raspbian:stretch
WORKDIR /app
COPY --from=0 /build/wheels wheels/

# RUN [ "cross-build-start" ]

RUN apt-get update -qq && \
    apt-get install libgtk2.0-dev libgtk-3-dev -y --no-install-recommends && \
    apt-get install python3 python3-dev python3-pip libraspberrypi-bin wiringpi -y --no-install-recommends \
    libjpeg-dev libtiff5-dev libjasper-dev libpng12-dev \
    libavcodec-dev libavformat-dev libswscale-dev libv4l-dev \
    libxvidcore-dev libx264-dev \
    libatlas-base-dev gfortran \
    openexr libilmbase12 libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libqtgui4 libqt4-test && \
    rm -rf /var/lib/apt/lists/*

COPY requirements* .

RUN pip3 install --extra-index-url https://www.piwheels.org/simple -r requirements.lock && \
    pip3 install gopigo3 Dexter_AutoDetection_and_I2C_Mutex --no-index --find-links=wheels && \
    rm -rf wheels
RUN usermod -aG video root

# RUN [ "cross-build-end" ]

COPY qa_config.json .
COPY logging.yaml .
COPY *.py ./

ENTRYPOINT ["tini", "-v", "--"]
CMD ["/usr/bin/python3", "main.py"]
