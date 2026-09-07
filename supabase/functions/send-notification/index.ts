import webpush from 'npm:web-push@3.6.7'
import { createClient } from 'npm:@supabase/supabase-js@2.56.1'

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Content-Type': 'application/json',
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: corsHeaders })
}

function adminKey() {
  const map = Deno.env.get('SUPABASE_SECRET_KEYS')
  if (map) {
    try {
      const parsed = JSON.parse(map)
      if (parsed?.default) return parsed.default
    } catch {}
  }
  return Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') || ''
}

async function ensurePushConfig(admin: ReturnType<typeof createClient>) {
  const existing = await admin.from('push_server_config').select('*').eq('id', true).maybeSingle()
  if (existing.data) return existing.data

  const keys = webpush.generateVAPIDKeys()
  const row = {
    id: true,
    vapid_public: keys.publicKey,
    vapid_private: keys.privateKey,
    vapid_subject: 'https://haimboch.github.io/Baby-shopping-list/',
  }
  const created = await admin.from('push_server_config').insert(row).select('*').single()
  if (created.data) return created.data

  const raced = await admin.from('push_server_config').select('*').eq('id', true).single()
  if (raced.error) throw raced.error
  return raced.data
}

async function sendToUser(
  admin: ReturnType<typeof createClient>,
  userId: string,
  payload: Record<string, unknown>,
) {
  const subscriptionsResult = await admin
    .from('push_subscriptions')
    .select('*')
    .eq('user_id', userId)
    .is('disabled_at', null)
  if (subscriptionsResult.error) throw subscriptionsResult.error

  const subscriptions = subscriptionsResult.data || []
  if (!subscriptions.length) {
    return { successes: 0, errors: [] as string[], noSubscription: true }
  }

  const cfg = await ensurePushConfig(admin)
  webpush.setVapidDetails(cfg.vapid_subject, cfg.vapid_public, cfg.vapid_private)
  const encodedPayload = JSON.stringify(payload)
  let successes = 0
  const errors: string[] = []

  for (const sub of subscriptions) {
    try {
      await webpush.sendNotification({
        endpoint: sub.endpoint,
        keys: { p256dh: sub.p256dh, auth: sub.auth_secret },
      }, encodedPayload, { TTL: 86400, urgency: 'high' })
      successes++
      await admin.from('push_subscriptions')
        .update({ last_success_at: new Date().toISOString(), disabled_at: null })
        .eq('id', sub.id)
    } catch (err: any) {
      const code = Number(err?.statusCode || err?.status || 0)
      const message = String(err?.message || err || 'push_failed').slice(0, 400)
      errors.push(`${code || 'error'}:${message}`)
      if (code === 404 || code === 410) {
        await admin.from('push_subscriptions')
          .update({ disabled_at: new Date().toISOString() })
          .eq('id', sub.id)
      }
    }
  }
  return { successes, errors, noSubscription: false }
}

export default {
  async fetch(req: Request) {
    if (req.method === 'OPTIONS') return new Response('ok', { headers: corsHeaders })
    if (req.method !== 'POST') return json({ error: 'method_not_allowed' }, 405)

    const supabaseUrl = Deno.env.get('SUPABASE_URL')
    const secretKey = adminKey()
    if (!supabaseUrl || !secretKey) return json({ error: 'server_not_configured' }, 500)

    const admin = createClient(supabaseUrl, secretKey, {
      auth: { persistSession: false, autoRefreshToken: false },
    })

    let body: Record<string, any>
    try { body = await req.json() } catch { return json({ error: 'invalid_json' }, 400) }

    try {
      if (body.action === 'config') {
        const cfg = await ensurePushConfig(admin)
        return json({ publicKey: cfg.vapid_public })
      }

      if (body.action === 'test') {
        const authorization = req.headers.get('authorization') || ''
        const token = authorization.match(/^Bearer\s+(.+)$/i)?.[1]
        if (!token) return json({ error: 'authentication_required' }, 401)
        const userResult = await admin.auth.getUser(token)
        if (userResult.error || !userResult.data.user) {
          return json({ error: 'invalid_session' }, 401)
        }
        const sent = await sendToUser(admin, userResult.data.user.id, {
          id: `push-test-${crypto.randomUUID()}`,
          title: '✅ בדיקת ההתראות הצליחה',
          body: 'המכשיר הזה מחובר ויקבל התראות גם כשהאפליקציה סגורה.',
          type: 'push_test',
          data: { action: 'open_notifications' },
          created_at: new Date().toISOString(),
        })
        if (sent.noSubscription) {
          return json({ error: 'no_active_subscription' }, 409)
        }
        if (!sent.successes) {
          return json({ error: 'push_provider_rejected', details: sent.errors }, 502)
        }
        return json({ ok: true, successes: sent.successes, failures: sent.errors.length })
      }

      if (body.action !== 'dispatch' || !body.notification_id || !body.dispatch_token) {
        return json({ error: 'invalid_request' }, 400)
      }

      const queueResult = await admin
        .from('notification_dispatch_queue')
        .select('*')
        .eq('notification_id', body.notification_id)
        .eq('dispatch_token', body.dispatch_token)
        .is('delivered_at', null)
        .maybeSingle()

      if (!queueResult.data) return json({ error: 'invalid_or_used_dispatch_token' }, 403)
      const queue = queueResult.data

      await admin.from('notification_dispatch_queue')
        .update({ attempts: Number(queue.attempts || 0) + 1 })
        .eq('notification_id', body.notification_id)

      const notificationResult = await admin.from('notifications').select('*').eq('id', body.notification_id).single()
      if (notificationResult.error || !notificationResult.data) {
        await admin.from('notification_dispatch_queue')
          .update({ delivered_at: new Date().toISOString(), last_error: 'notification_not_found' })
          .eq('notification_id', body.notification_id)
        return json({ error: 'notification_not_found' }, 404)
      }
      const notification = notificationResult.data

      const prefResult = await admin.from('notification_preferences')
        .select('push_enabled')
        .eq('user_id', notification.user_id)
        .eq('household_id', notification.household_id)
        .maybeSingle()

      if (prefResult.data?.push_enabled === false) {
        await admin.from('notification_dispatch_queue')
          .update({ delivered_at: new Date().toISOString(), last_error: null })
          .eq('notification_id', notification.id)
        return json({ ok: true, skipped: 'push_disabled' })
      }

      const sent = await sendToUser(admin, notification.user_id, {
        id: notification.id,
        title: notification.title,
        body: notification.body,
        type: notification.notification_type,
        product_id: notification.product_id,
        data: notification.data || {},
        created_at: notification.created_at,
      })
      if (sent.noSubscription) {
        const now = new Date().toISOString()
        await admin.from('notifications')
          .update({ push_sent_at: null, push_error: 'no_active_subscription' })
          .eq('id', notification.id)
        await admin.from('notification_dispatch_queue')
          .update({ delivered_at: now, last_error: 'no_active_subscription' })
          .eq('notification_id', notification.id)
        return json({ ok: true, skipped: 'no_active_subscription' })
      }

      const now = new Date().toISOString()
      await admin.from('notifications').update({
        push_sent_at: sent.successes > 0 ? now : null,
        push_error: sent.errors.length ? sent.errors.join(' | ').slice(0, 1200) : null,
      }).eq('id', notification.id)

      await admin.from('notification_dispatch_queue').update({
        delivered_at: now,
        last_error: sent.successes > 0 || sent.errors.length === 0 ? null : sent.errors.join(' | ').slice(0, 1200),
      }).eq('notification_id', notification.id)

      return json({ ok: true, successes: sent.successes, failures: sent.errors.length })
    } catch (err: any) {
      return json({ error: String(err?.message || err || 'unknown_error').slice(0, 800) }, 500)
    }
  },
}
