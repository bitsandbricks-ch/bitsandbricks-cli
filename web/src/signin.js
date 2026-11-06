// GitHub's device flow from the browser: ask Mason for a code, show it, then poll Mason (which asks
// GitHub) until the user has entered the code at github.com/login/device. Mason keeps GitHub's device
// code; the browser only holds an HttpOnly cookie for this one sign-in.
import { post } from './api.js'

export function deviceSignIn(provider, { next = '/', link = false } = {}, { code, done, fail }) {
  let stopped = false
  let timer = null
  const stop = () => { stopped = true; clearTimeout(timer) }
  const wait = (seconds) => { timer = setTimeout(poll, Math.max(1, seconds) * 1000) }

  async function poll() {
    if (stopped) return
    try {
      const got = await post(`/auth/${provider}/device/poll`)
      if (stopped) return
      if (got.status === 'done') { stop(); done(got.next || '/'); return }
      wait(got.interval || 5)
    } catch (e) {
      if (stopped) return
      if (e.status === 502) { wait(10); return }  // GitHub didn't answer: try again
      stop(); fail(e.message)
    }
  }

  post(`/auth/${provider}/device`, { next, link }).then((got) => {
    if (stopped) return
    code(got)
    wait(got.interval || 5)
  }).catch((e) => { if (!stopped) { stop(); fail(e.message) } })
  return stop
}
