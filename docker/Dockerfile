FROM python:3.11
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ="Europe/Berlin"
RUN mkdir /fixtures
WORKDIR /code
COPY ./requirements.txt /code/
RUN pip install -U pip
RUN pip install -r requirements.txt
COPY ./src /code/
COPY ./data/codebook.pdf /code/
