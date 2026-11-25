import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { COLUMNS, type ColumnKey, type FlagOut } from '../../core/api/api.types';
import { I18n } from '../../core/i18n/i18n';
import { flagText, type NoteDraft, RecordEditor, type ReviewRecord } from './record-editor';

function flag(over: Partial<FlagOut>): FlagOut {
  return {
    id: 1,
    page: 1,
    row: 1,
    column: 'species',
    source: 'llm',
    severity: 'warning',
    code: null,
    params: {},
    message: 'unclear',
    suggestion: null,
    confidence: null,
    resolved: false,
    ...over,
  };
}

describe('RecordEditor', () => {
  let fixture: ComponentFixture<RecordEditor>;
  let editor: RecordEditor;
  let i18n: I18n;
  let t: I18n['t'];
  const el = () => fixture.nativeElement as HTMLElement;
  const buttonByText = (text: string) =>
    [...el().querySelectorAll('button')].find((b) => b.textContent?.trim() === text);

  const llm = flag({
    id: 1,
    column: 'species',
    source: 'llm',
    message: 'Hard to read',
    params: { value: 'Amsl' },
    confidence: 0.42,
    suggestion: 'Amsel',
  });
  const rule = flag({
    id: 2,
    column: 'weight_g',
    source: 'rule',
    severity: 'error',
    code: 'weight_out_of_range',
    params: { value: '300', min: 5, max: 40 },
    message: 'raw backend text',
  });
  const note = flag({ id: 3, column: null, source: 'user', message: 'Check ring number' });

  function render(
    record: ReviewRecord,
    values: Partial<Record<ColumnKey, string>> = { species: 'Amsl', weight_g: '300' },
  ) {
    fixture.componentRef.setInput('record', record);
    fixture.componentRef.setInput('values', values);
    fixture.detectChanges();
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [RecordEditor] });
    i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
    t = i18n.t.bind(i18n);
    fixture = TestBed.createComponent(RecordEditor);
    editor = fixture.componentInstance;
    render({ key: '1:4', page: 1, row: 4, flags: [llm, rule, note] });
  });

  it('shows page and row', () => {
    expect(el().textContent).toContain(
      `${t('review.page', { n: 1 })} · ${t('review.row', { n: 4 })}`,
    );
  });

  describe('flags', () => {
    const blocks = () =>
      [...el().querySelectorAll<HTMLElement>('div.rounded-md.border')].filter((d) =>
        d.querySelector('[class*="uppercase"]'),
      );

    it('shows one block per flag with its source label', () => {
      const b = blocks();
      expect(b.length).toBe(3);
      expect(b[0].textContent).toContain(t('source.llm'));
      expect(b[1].textContent).toContain(t('source.rule'));
      expect(b[2].textContent).toContain(t('source.user'));
    });

    it('translates rule flags from code and params; AI and note messages are shown as written', () => {
      const b = blocks();
      expect(b[0].textContent).toContain('Hard to read');
      expect(b[1].textContent).toContain(
        t('flag.weight_out_of_range', { value: '300', min: 5, max: 40 }),
      );
      expect(b[1].textContent).not.toContain('raw backend text');
      expect(b[2].textContent).toContain('Check ring number');
    });

    it('shows the column of column flags', () => {
      expect(blocks()[0].textContent).toContain(`${t('col.species')}:`);
    });

    it('shows the value read and the confidence of AI flags', () => {
      const text = blocks()[0].textContent!;
      expect(text).toContain(t('review.read', { value: 'Amsl' }));
      expect(text).toContain(t('review.confidence', { pct: 42 }));
    });

    it('colours errors red, warnings amber and notes blue', () => {
      const b = blocks();
      expect(b[0].className).toContain('border-amber-300');
      expect(b[1].className).toContain('border-red-300');
      expect(b[2].className).toContain('border-sky-300');
    });

    it('fills the field when the suggestion is clicked', () => {
      const changed = vi.fn();
      editor.changed.subscribe(changed);
      [...el().querySelectorAll('button')].find((b) => b.textContent?.includes('„Amsel“'))!.click();
      expect(changed).toHaveBeenCalledWith({ column: 'species', value: 'Amsel' });
    });

    it('offers delete only for reviewer notes', () => {
      const del = vi.fn();
      editor.deleteNote.subscribe(del);
      const buttons = el().querySelectorAll<HTMLButtonElement>(
        `button[aria-label="${t('review.deleteNote')}"]`,
      );
      expect(buttons.length).toBe(1);
      buttons[0].click();
      expect(del).toHaveBeenCalledWith(3);
    });

    it('shows a hint for rows without open flags', () => {
      render({ key: '1:9', page: 1, row: 9, flags: [] });
      expect(el().textContent).toContain(t('review.noFlags'));
    });
  });

  describe('fields', () => {
    it('has one field per column, prefilled with the values', () => {
      for (const c of COLUMNS) expect(el().querySelector(`#field-${c}`)).toBeTruthy();
      expect(el().querySelector<HTMLInputElement>('#field-species')!.value).toBe('Amsl');
      expect(el().querySelector<HTMLInputElement>('#field-ring_no')!.value).toBe('');
      expect(el().querySelector('#field-additional_remark')!.tagName).toBe('TEXTAREA');
    });

    it('highlights flagged fields', () => {
      expect(el().querySelector('#field-species')!.classList).toContain('ring-amber-400');
      expect(el().querySelector('#field-weight_g')!.classList).toContain('ring-amber-400');
      expect(el().querySelector('#field-sex')!.classList).not.toContain('ring-amber-400');
    });

    it('emits every change', () => {
      const changed = vi.fn();
      editor.changed.subscribe(changed);
      const input = el().querySelector<HTMLInputElement>('#field-sex')!;
      input.value = 'F';
      input.dispatchEvent(new Event('input'));
      expect(changed).toHaveBeenCalledWith({ column: 'sex', value: 'F' });
    });

    it('Save & next emits save, Skip emits skip', () => {
      const save = vi.fn(),
        skip = vi.fn();
      editor.save.subscribe(save);
      editor.skip.subscribe(skip);
      buttonByText(t('review.save'))!.click();
      buttonByText(t('review.skip'))!.click();
      expect(save).toHaveBeenCalledOnce();
      expect(skip).toHaveBeenCalledOnce();
    });

    it('disables the actions while busy and shows save errors', () => {
      fixture.componentRef.setInput('busy', true);
      fixture.componentRef.setInput('error', true);
      fixture.detectChanges();
      expect(buttonByText(t('review.save'))!.disabled).toBe(true);
      expect(buttonByText(t('review.skip'))!.disabled).toBe(true);
      expect(el().textContent).toContain(t('review.saveFailed'));
    });
  });

  describe('add note', () => {
    function openForm() {
      buttonByText(t('review.addNote'))!.click();
      fixture.detectChanges();
      return el().querySelector<HTMLTextAreaElement>('form textarea[placeholder]')!;
    }

    it('emits the note for the chosen column and closes the form', () => {
      const added = vi.fn<(n: NoteDraft) => void>();
      editor.addNote.subscribe(added);
      const textarea = openForm();
      const select = el().querySelector<HTMLSelectElement>('form select')!;
      select.value = 'ring_no';
      select.dispatchEvent(new Event('change'));
      textarea.value = '  Ring looks off  ';
      textarea.dispatchEvent(new Event('input'));
      fixture.detectChanges();
      el().querySelector('form')!.dispatchEvent(new Event('submit'));
      fixture.detectChanges();

      expect(added).toHaveBeenCalledWith({ column: 'ring_no', message: 'Ring looks off' });
      expect(editor.noteOpen()).toBe(false);
      expect(el().querySelector('form textarea[placeholder]')).toBeNull();
    });

    it('uses the preselected column, or the whole row', () => {
      const added = vi.fn<(n: NoteDraft) => void>();
      editor.addNote.subscribe(added);
      fixture.componentRef.setInput('noteColumn', 'date');
      const textarea = openForm();
      expect(el().querySelector<HTMLSelectElement>('form select')!.value).toBe('date');

      const select = el().querySelector<HTMLSelectElement>('form select')!;
      select.value = '';
      select.dispatchEvent(new Event('change'));
      textarea.value = 'whole row';
      textarea.dispatchEvent(new Event('input'));
      el().querySelector('form')!.dispatchEvent(new Event('submit'));
      expect(added).toHaveBeenCalledWith({ column: null, message: 'whole row' });
    });

    it('does not emit an empty note', () => {
      const added = vi.fn();
      editor.addNote.subscribe(added);
      openForm();
      fixture.detectChanges();
      const submit = el().querySelector<HTMLButtonElement>('form button[type="submit"]')!;
      expect(submit.disabled).toBe(true);
      el().querySelector('form')!.dispatchEvent(new Event('submit'));
      expect(added).not.toHaveBeenCalled();
    });

    it('can be cancelled', () => {
      openForm();
      buttonByText(t('upload.cancel'))!.click();
      fixture.detectChanges();
      expect(el().querySelector('form textarea[placeholder]')).toBeNull();
    });
  });
});

describe('flagText', () => {
  it('translates known rule codes and falls back to the message', () => {
    TestBed.configureTestingModule({});
    const i18n = TestBed.inject(I18n);
    i18n.lang.set('en');
    expect(
      flagText(i18n, flag({ source: 'rule', code: 'time_format', params: { value: '25:99' } })),
    ).toBe(i18n.t('flag.time_format', { value: '25:99' }));
    expect(
      flagText(i18n, flag({ source: 'rule', code: 'unknown_code', message: 'fallback' })),
    ).toBe('fallback');
    expect(
      flagText(i18n, flag({ source: 'llm', code: 'time_format', message: 'as written' })),
    ).toBe('as written');
  });
});
