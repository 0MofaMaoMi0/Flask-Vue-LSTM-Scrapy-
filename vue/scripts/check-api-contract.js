/* Verify that active Vue API declarations map to implemented Flask routes. */
const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..', '..');
const frontendDir = path.join(projectRoot, 'vue', 'src', 'api');
const backendDir = path.join(projectRoot, 'Flask');
const blueprintPrefixes = {
  'movieApi.py': '/movie',
  'userApi.py': '/user',
  'orderApi.py': '/order',
  'alipayApi.py': '/alipay',
  'testApi.py': '/test',
};

function routesFromBackend(file, prefix = '') {
  const source = fs.readFileSync(file, 'utf8');
  const result = new Set();
  const routePattern = /@(?:\w+\.)?route\(\s*['"]([^'"]+)['"][\s\S]{0,100}?methods\s*=\s*\[\s*['"]([A-Z]+)['"]/g;
  for (const match of source.matchAll(routePattern)) {
    const normalizedPath = `${prefix}${match[1]}`.replace(/<[^>]+>/g, '');
    result.add(`${match[2]} ${normalizedPath}`);
  }
  return result;
}

const backendRoutes = new Set();
for (const [fileName, prefix] of Object.entries(blueprintPrefixes)) {
  for (const route of routesFromBackend(path.join(backendDir, 'api', fileName), prefix)) {
    backendRoutes.add(route);
  }
}
for (const route of routesFromBackend(path.join(backendDir, 'app.py'))) {
  backendRoutes.add(route);
}

const frontendRoutes = [];
for (const fileName of fs.readdirSync(frontendDir).filter(name => name.endsWith('.js'))) {
  const file = path.join(frontendDir, fileName);
  const source = fs.readFileSync(file, 'utf8');
  if (source.includes('legacy-api: ignore-contract')) {
    continue;
  }
  const baseMatch = source.match(/const base = ['"]([^'"]*)['"]/);
  if (!baseMatch) {
    continue;
  }
  const routePattern = /url:\s*base\s*\+\s*['"]([^'"]+)['"][\s\S]{0,80}?method:\s*['"]([a-z]+)['"]/g;
  for (const match of source.matchAll(routePattern)) {
    frontendRoutes.push({
      file: fileName,
      method: match[2].toUpperCase(),
      path: `${baseMatch[1]}${match[1]}`,
    });
  }
}

const missing = frontendRoutes.filter(route => !backendRoutes.has(`${route.method} ${route.path}`));
if (missing.length) {
  for (const route of missing) {
    console.error(`Missing backend route: ${route.method} ${route.path} (declared in ${route.file})`);
  }
  process.exitCode = 1;
} else {
  console.log(`API contract passed: ${frontendRoutes.length} active Vue declarations map to Flask routes.`);
}
