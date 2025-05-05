FROM python:3.9-slim

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get -qq update \
    && apt-get -qq install --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install pipenv

RUN mkdir -p /app
COPY Pipfile* /app/

RUN cd /app && pipenv requirements > requirements.txt
RUN pip install -r /app/requirements.txt

WORKDIR /app

COPY . /app/

ENV ASR_MODEL small

VOLUME /app/files

CMD  [ "python", "./run.py" ]