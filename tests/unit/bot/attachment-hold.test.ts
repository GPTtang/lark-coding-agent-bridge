import type { NormalizedMessage } from '@larksuite/channel';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AttachmentHold,
  isAttachmentOnly,
  stripAttachmentRefs,
} from '../../../src/bot/attachment-hold.js';

describe('AttachmentHold', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('holds attachment-only messages until released by the next text message', () => {
    vi.useFakeTimers();
    const expired: Array<{ scope: string; held: NormalizedMessage[] }> = [];
    const hold = new AttachmentHold((scope, held) => expired.push({ scope, held }));

    expect(hold.hold('chat-1', msg('m-1'), 60_000)).toBe(1);
    expect(hold.hold('chat-1', msg('m-2'), 60_000)).toBe(2);
    vi.advanceTimersByTime(59_999);

    expect(hold.release('chat-1')).toEqual([msg('m-1'), msg('m-2')]);
    expect(hold.release('chat-1')).toEqual([]);
    vi.advanceTimersByTime(60_000);
    expect(expired).toEqual([]);
  });

  it('hands held messages to onExpire when no text arrives within the window', () => {
    vi.useFakeTimers();
    const expired: Array<{ scope: string; held: NormalizedMessage[] }> = [];
    const hold = new AttachmentHold((scope, held) => expired.push({ scope, held }));

    hold.hold('chat-1', msg('m-1'), 1_000);
    vi.advanceTimersByTime(600);
    hold.hold('chat-1', msg('m-2'), 1_000); // re-arms the window
    vi.advanceTimersByTime(999);
    expect(expired).toEqual([]);
    vi.advanceTimersByTime(1);

    expect(expired).toEqual([{ scope: 'chat-1', held: [msg('m-1'), msg('m-2')] }]);
    expect(hold.release('chat-1')).toEqual([]);
  });

  it('keeps scopes independent and cancelAll drops every pending timer', () => {
    vi.useFakeTimers();
    const expired: string[] = [];
    const hold = new AttachmentHold((scope) => expired.push(scope));

    hold.hold('chat-1', msg('m-1'), 1_000);
    hold.hold('chat-2', msg('m-2'), 1_000);
    expect(hold.release('chat-2')).toEqual([msg('m-2')]);
    hold.cancelAll();
    vi.advanceTimersByTime(5_000);

    expect(expired).toEqual([]);
    expect(hold.release('chat-1')).toEqual([]);
  });
});

describe('isAttachmentOnly', () => {
  it('is true only when a message has resources and no remaining text', () => {
    expect(isAttachmentOnly(msg('m-1', '![image](img_k1)', ['img_k1']))).toBe(true);
    expect(isAttachmentOnly(msg('m-2', '<image key="img_k2"/>\n', ['img_k2']))).toBe(true);
    expect(isAttachmentOnly(msg('m-3', '![image](img_k3) 这是什么', ['img_k3']))).toBe(false);
    expect(isAttachmentOnly(msg('m-4', 'plain text', []))).toBe(false);
    expect(isAttachmentOnly(msg('m-5', '', []))).toBe(false);
  });

  it('strips markdown and tag style attachment references', () => {
    expect(stripAttachmentRefs('a ![x](k1) b <file key="k2"/>', ['k1', 'k2'])).toBe('a  b ');
    expect(stripAttachmentRefs('keep', [])).toBe('keep');
  });
});

function msg(messageId: string, content = '![image](img_x)', fileKeys = ['img_x']): NormalizedMessage {
  return {
    messageId,
    chatId: 'chat-1',
    chatType: 'group',
    senderId: 'ou-user',
    content,
    resources: fileKeys.map((fileKey) => ({ type: 'image', fileKey })),
    mentionedBot: true,
  } as unknown as NormalizedMessage;
}
