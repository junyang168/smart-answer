docker run --detach --name vespa --hostname vespa-container \
  --publish 8080:8080 \
  --publish 19071:19071 \
  --volume $VESPA_LOG_STORAGE:/opt/vespa/logs \
  --volume $VESPA_VAR_STORAGE:/opt/vespa/var \
  vespaengine/vespa

docker ps
docker stop id
docker update -m 32g --memory-swap -1 id
docker start id  
vespa deploy --wait 500

