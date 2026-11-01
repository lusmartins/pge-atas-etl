# Imagem do Airflow + as bibliotecas do nosso projeto.
# Partimos da imagem oficial (mesma versão que usamos no estudo) e só acrescentamos o que falta.
FROM apache/airflow:2.10.5

# o código fica em /opt/airflow/src; dizemos ao Python para procurar lá ("from src import ...")
ENV PYTHONPATH=/opt/airflow

# pasta onde as etapas trocam arquivos; criada aqui para o usuário "airflow" poder escrever nela
USER root
RUN mkdir -p /opt/airflow/data && chown airflow /opt/airflow/data
USER airflow

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
