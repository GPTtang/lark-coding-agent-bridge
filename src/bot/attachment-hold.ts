import type { NormalizedMessage } from '@larksuite/channel';

interface HoldEntry {
  messages: NormalizedMessage[];
  timer: NodeJS.Timeout;
}

export type HoldExpireHandler = (scope: string, held: NormalizedMessage[]) => void;

/**
 * Per-scope parking for attachment-only messages (a bare screenshot / file).
 *
 * Feishu clients send an image and the question about it as two separate
 * messages, often more than the 600ms debounce window apart. Without this
 * hold the image alone flushes as "请看下面的附件。" and the follow-up question
 * becomes a second, context-less run. Held messages are prepended to the
 * scope's next text message via `release`; if no text arrives within the
 * window, `onExpire` hands them back so the attachment still gets processed.
 */
export class AttachmentHold {
  private readonly map = new Map<string, HoldEntry>();
  private readonly onExpire: HoldExpireHandler;

  constructor(onExpire: HoldExpireHandler) {
    this.onExpire = onExpire;
  }

  /** Park a message and (re)arm the scope's window; returns how many are held. */
  hold(scope: string, msg: NormalizedMessage, windowMs: number): number {
    const existing = this.map.get(scope);
    if (existing) clearTimeout(existing.timer);
    const messages = [...(existing?.messages ?? []), msg];
    const timer = setTimeout(() => {
      this.map.delete(scope);
      this.onExpire(scope, messages);
    }, windowMs);
    this.map.set(scope, { messages, timer });
    return messages.length;
  }

  /** Take everything held for a scope (empty when nothing is held). */
  release(scope: string): NormalizedMessage[] {
    const entry = this.map.get(scope);
    if (!entry) return [];
    clearTimeout(entry.timer);
    this.map.delete(scope);
    return entry.messages;
  }

  cancelAll(): void {
    for (const entry of this.map.values()) clearTimeout(entry.timer);
    this.map.clear();
  }
}

/** True when the message carries attachments and no text of its own. */
export function isAttachmentOnly(msg: NormalizedMessage): boolean {
  if (msg.resources.length === 0) return false;
  const fileKeys = msg.resources.map((r) => r.fileKey);
  return stripAttachmentRefs(msg.content, fileKeys).trim() === '';
}

export function stripAttachmentRefs(text: string, fileKeys: string[]): string {
  if (!text || fileKeys.length === 0) return text;
  let out = text;
  for (const key of fileKeys) {
    const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    out = out.replace(new RegExp(`!?\\[[^\\]]*\\]\\(${escaped}\\)`, 'g'), '');
    out = out.replace(
      new RegExp(
        `<\\s*(?:file|image|img|audio|video|media|folder)\\b[^>]*\\bkey\\s*=\\s*["']${escaped}["'][^>]*>`,
        'gi',
      ),
      '',
    );
  }
  return out.replace(/\n{3,}/g, '\n\n');
}
