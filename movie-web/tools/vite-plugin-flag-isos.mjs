// 旗帜可用性虚拟模块：把 public/m9/globe/flags 下真实存在的合法三字母 SVG 以代码集合
// 形式提供给前端，避免给缺图国家拼出 404（或被 SPA 兜底成 HTML）的图片地址。
// 单一事实来源是目录本身：启动、构建以及每次 load 都重新枚举，不维护第二份清单、
// 不 import 图片、不内联、不发射副本，也不硬编码国家数量或缺失码。
import fs from 'node:fs';
import path from 'node:path';

export const VIRTUAL_ID = 'virtual:flag-isos';
const RESOLVED_ID = '\0' + VIRTUAL_ID;
const FLAG_FILE = /^([A-Z]{3})\.svg$/;

function flagsDirOf(publicDir) {
  return publicDir ? path.join(publicDir, 'm9', 'globe', 'flags') : '';
}

function readFlagIsos(publicDir) {
  const dir = flagsDirOf(publicDir);
  if (!dir) return [];
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return []; // publicDir 关闭或目录缺失：安全空集合，不中断 dev/build
  }
  const codes = [];
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const match = FLAG_FILE.exec(entry.name);
    if (match) codes.push(match[1]);
  }
  return codes.sort();
}

export function flagIsosPlugin() {
  let publicDir = '';
  return {
    name: 'mi2-flag-isos',
    configResolved(config) {
      publicDir = config.publicDir || '';
    },
    resolveId(id) {
      return id === VIRTUAL_ID ? RESOLVED_ID : null;
    },
    load(id) {
      if (id !== RESOLVED_ID) return null;
      // 每次 load 都重扫真实目录：启动扫描窗口内新增的文件、或 watcher 被关掉的场景，
      // 只要模块被重新请求就不会拿到永久陈旧的列表。
      const codes = readFlagIsos(publicDir);
      return `export const FLAG_ISO3 = Object.freeze(${JSON.stringify(codes)});\nexport default FLAG_ISO3;\n`;
    },
    configureServer(server) {
      const dir = flagsDirOf(publicDir);
      if (!dir || !server.watcher) return;
      const isFlag = (file) =>
        FLAG_FILE.test(path.basename(file)) && path.resolve(path.dirname(file)) === path.resolve(dir);
      const invalidate = () => {
        const mod = server.moduleGraph.getModuleById(RESOLVED_ID);
        if (mod) server.moduleGraph.invalidateModule(mod);
        server.ws?.send?.({ type: 'full-reload' });
      };
      const onAdd = (file) => isFlag(file) && invalidate();
      const onUnlink = (file) => isFlag(file) && invalidate();
      server.watcher.add(dir);
      server.watcher.on('add', onAdd);
      server.watcher.on('unlink', onUnlink);
      // 程序化复用同一进程重建服务时解除监听，避免重复注册。
      server.httpServer?.once('close', () => {
        server.watcher.off('add', onAdd);
        server.watcher.off('unlink', onUnlink);
      });
    },
  };
}

export default flagIsosPlugin;