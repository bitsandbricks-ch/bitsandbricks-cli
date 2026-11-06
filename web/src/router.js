import { createRouter, createWebHistory } from 'vue-router'

const v = (name) => () => import(`./views/${name}.vue`)

export const TABS = [
  { path: '/', name: 'repos', label: 'repos', key: '1' },
  { path: '/prs', name: 'prs', label: 'pull requests', key: '2' },
  { path: '/issues', name: 'issues', label: 'issues', key: 'I' },
  { path: '/runs', name: 'runs', label: 'runs', key: '3' },
  { path: '/approvals', name: 'approvals', label: 'approvals', key: '4' },
  { path: '/releases', name: 'releases', label: 'releases', key: '5' },
  { path: '/packages', name: 'packages', label: 'packages', key: '6' },
  { path: '/security', name: 'security', label: 'security', key: '7' },
  { path: '/checks', name: 'checks', label: 'checks', key: '8' },
  { path: '/workflows', name: 'workflows', label: 'workflows', key: 'W' },
  { path: '/stats', name: 'stats', label: 'stats', key: '9' },
  { path: '/defaults', name: 'defaults', label: 'defaults', key: 'D' },
  { path: '/console', name: 'console', label: 'console', key: ':' },
  { path: '/plugins', name: 'plugins', label: 'plugins', key: 'P' },
  { path: '/market', name: 'market', label: 'market', key: 'K' },
  { path: '/settings', name: 'settings', label: 'settings', key: 'S' },
  { path: '/audit', name: 'audit', label: 'audit', key: 'L' },
]

const VIEWS = {
  repos: 'Repos', prs: 'Prs', issues: 'Issues', runs: 'Runs', approvals: 'Approvals', releases: 'Releases', packages: 'Packages',
  security: 'Security', checks: 'Checks', workflows: 'Workflows', stats: 'Stats', defaults: 'Defaults', console: 'Console',
  plugins: 'Plugins', market: 'Market', settings: 'Settings', audit: 'Audit',
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: v('Login'), meta: { public: true } },
    { path: '/device', name: 'device', component: v('Device') },
    ...TABS.map((t) => ({ path: t.path, name: t.name, component: v(VIEWS[t.name]) })),
    { path: '/repo', name: 'repo', component: v('RepoPage'), meta: { tab: 'repos' } },
    { path: '/release', name: 'release', component: v('ReleasePage'), meta: { tab: 'releases' } },
    { path: '/plugins/:plugin/:tab', name: 'plugin-tab', component: v('PluginTab') },
    { path: '/operator', name: 'operator', component: v('Operator') },
    { path: '/:rest(.*)*', redirect: '/' },
  ],
})
