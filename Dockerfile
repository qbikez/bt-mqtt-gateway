FROM python:3.8-alpine3.12

ENV DEBUG false

RUN mkdir application
WORKDIR /application

RUN apk add --no-cache tzdata bluez bluez-libs sudo bluez-deprecated
RUN apk add --no-cache --virtual build-dependencies git bluez-dev musl-dev make gcc glib-dev musl-dev

RUN ln -s /config.yaml ./config.yaml

COPY requirements.txt /application
RUN pip install -r requirements.txt

COPY requirements-workers.txt /application
RUN pip install -r requirements-workers.txt

COPY . /application

# Run this to install additional workers' requirements
# or put them in requirements-workers.txt to avoid unnecessary rebuild
# RUN chmod u+x ./gateway.py && pip install `./gateway.py -r all`

# RUN apk del build-dependencies

COPY ./start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/bin/sh", "-c", "/start.sh"]
