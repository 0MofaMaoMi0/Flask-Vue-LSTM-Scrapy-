import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import net from 'node:net';
const web=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),root=path.resolve(web,'..');
const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',TEMP:path.join(root,'.local-cache/tmp'),TMP:path.join(root,'.local-cache/tmp'),MI2_RATE_WINDOW:'30',MI2_PORT:'8092',MI2_ALLOWED_ORIGINS:'http://127.0.0.1:5175',MI2_API_TARGET:'http://127.0.0.1:8092',MI2_E2E_BASE_URL:'http://127.0.0.1:5175'};
for(const port of [8092,5175])await new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',()=>reject(Error('E2E port occupied: '+port)));s.listen(port,'127.0.0.1',()=>s.close(resolve))});
const children=[];
function start(command,args,cwd){const p=spawn(command,args,{cwd,env,windowsHide:true,stdio:'ignore'});children.push(p);return p}
try{
 start(path.join(root,'.local-cache/auth-venv/Scripts/python.exe'),[path.join(root,'backend/manage.py'),'--test'],root);
 start(process.execPath,[path.join(web,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','5175'],web);
 let ready=false;
 for(let i=0;i<100;i++){try{const r=await fetch(env.MI2_E2E_BASE_URL+'/api/v1/health');if(r.ok){ready=true;break}}catch{}await new Promise(r=>setTimeout(r,100))}
 if(!ready)throw Error('Isolated E2E services did not become ready');
 const p=spawn(process.execPath,[path.join(web,'tools/auth-e2e.mjs')],{cwd:web,env,windowsHide:true,stdio:'inherit'});
 process.exitCode=await new Promise(resolve=>p.on('exit',code=>resolve(code??1)));
}finally{for(const p of children)p.kill()}
