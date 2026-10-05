FROM python:3.11-slim

RUN useradd --create-home --uid 10001 runner
WORKDIR /opt/rlab
COPY verify_candidate.py /opt/rlab/verify_candidate.py

USER runner
ENTRYPOINT ["python", "/opt/rlab/verify_candidate.py"]
