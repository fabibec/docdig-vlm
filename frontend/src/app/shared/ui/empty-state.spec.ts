import { ChangeDetectionStrategy, Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, it, expect } from 'vitest';

import { EmptyState } from './empty-state';

@Component({
  selector: 'app-empty-host',
  imports: [EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<app-empty-state icon="lucideCircleCheck" tone="text-green-600" title="All done" text="Nothing left">
    <button id="action">Back</button>
  </app-empty-state>`,
})
class EmptyHost {}

describe('EmptyState', () => {
  it('shows the title, the optional text and projected content', () => {
    const fixture = TestBed.createComponent(EmptyHost);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('h2')!.textContent).toBe('All done');
    expect(el.querySelector('p')!.textContent).toBe('Nothing left');
    expect(el.querySelector('#action')).toBeTruthy();
    const icon = el.querySelector('ng-icon')!;
    expect(icon.getAttribute('class')).toContain('text-green-600');
  });

  it('omits the text paragraph and uses the inbox icon by default', () => {
    const fixture = TestBed.createComponent(EmptyState);
    fixture.componentRef.setInput('title', 'Empty');
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('h2')!.textContent).toBe('Empty');
    expect(el.querySelector('p')).toBeNull();
    expect(fixture.componentInstance.icon()).toBe('lucideInbox');
    expect(el.querySelector('ng-icon')!.getAttribute('class')).toContain('text-muted-foreground');
  });
});
