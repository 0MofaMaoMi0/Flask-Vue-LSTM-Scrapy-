"""One-time, explicitly authorized guest-only M7 auxiliary provisioning."""
import json,subprocess,secrets,os
from pathlib import Path
UUID='01287100-d9e6-11f1-9713-000c29c02265'
DMI='3f614d56-7436-7a96-c8ea-a786bcc02265'
SCHEMA='mi2_m7_assistant_20261107';USER='mi2m7app'
def sql(q):
    p=subprocess.run(['/usr/bin/mysql','--protocol=socket','-N'],input=q.encode(),capture_output=True)
    if p.returncode:raise RuntimeError('M7_SQL_FAILED')
    return p.stdout.decode().strip()
if os.geteuid()!=0:raise SystemExit('Run inside the specified Ubuntu VM with sudo')
if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=DMI or sql('SELECT @@server_uuid')!=UUID:raise SystemExit('IDENTITY_MISMATCH')
private=Path('/etc/movieinsight-m7');private.mkdir(mode=0o700,exist_ok=True)
if (private/'app.env').exists():raise SystemExit('ALREADY_PROVISIONED_REVIEW_REQUIRED')
if sql("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='"+SCHEMA+"'"):raise SystemExit('NONEMPTY_TARGET_REFUSED')
password=secrets.token_hex(32)
sql('CREATE DATABASE `'+SCHEMA+'` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
DDL_MARKER = ['\nCREATE TABLE budgets (\n\tid VARCHAR(96) NOT NULL, \n\tcalls INTEGER NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n', '\nCREATE TABLE cache (\n\tid VARCHAR(64) NOT NULL, \n\tresponse JSON NOT NULL, \n\texpires_at DATETIME NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n', '\nCREATE TABLE requests (\n\tid VARCHAR(64) NOT NULL, \n\tuser_id BIGINT NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tresponse JSON, \n\texpires_at DATETIME NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n', '\nCREATE TABLE sessions (\n\tid VARCHAR(64) NOT NULL, \n\tuser_id BIGINT NOT NULL, \n\tauth_binding VARCHAR(64) NOT NULL, \n\tstate JSON NOT NULL, \n\tturns INTEGER NOT NULL, \n\texpires_at DATETIME NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n', '\nCREATE TABLE `usage` (\n\tid VARCHAR(64) NOT NULL, \n\tuser_id BIGINT NOT NULL, \n\tmodel VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(32) NOT NULL, \n\ttokens JSON, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n']
for ddl in DDL_MARKER:sql('USE `'+SCHEMA+'`;'+ddl)
sql("CREATE USER '"+USER+"'@'127.0.0.1' IDENTIFIED WITH caching_sha2_password BY '"+password+"'; GRANT SELECT,INSERT,UPDATE,DELETE ON `"+SCHEMA+"`.* TO '"+USER+"'@'127.0.0.1'")
env=private/'app.env';env.write_text('MI2_M7_DATABASE_URL=mysql+pymysql://'+USER+':'+password+'@127.0.0.1:3306/'+SCHEMA+'\n');env.chmod(0o600)
dropin=Path('/etc/systemd/system/movieinsight-m6.service.d');dropin.mkdir(exist_ok=True)
(dropin/'m7.conf').write_text('[Service]\nEnvironmentFile=/etc/movieinsight-m7/app.env\n')
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
(private/'provisioned.json').write_text(json.dumps({'schema':SCHEMA,'uuid':UUID,'cloud_enabled':False,'rollback':'Remove only m7.conf and restart application to disable M7; preserve auxiliary schema for review.'}))
print('M7_AUXILIARY_READY_NO_SECRETS_PRINTED')
