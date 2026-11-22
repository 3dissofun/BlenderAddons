import uuid

def debugUuids(datablocks):
    for db in datablocks:
        curUuid = db.uuid
        print(f"{db.name}:{curUuid}")

def ensureUuids(datablocks):
    seen = set()
    for db in datablocks:
        currentUuid = db.uuid
        if not currentUuid or currentUuid in seen:
            newUuid = str(uuid.uuid4())
            db.uuid = newUuid
            print(f"UUIDS: INFO, Assigned new uuid '{newUuid}' to datablock '{db}'")
        else:
            seen.add(currentUuid)
