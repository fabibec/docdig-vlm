import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { I18n, LANGS, Lang, TPipe } from '../core/i18n/i18n';
import { Realtime } from '../core/realtime/realtime.service';
import { THEME_MODES, Theme, ThemeMode } from '../core/theme/theme.service';
import { Logo } from './logo';

@Component({
  selector: 'app-top-bar',
  imports: [RouterLink, RouterLinkActive, TPipe, Logo],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <header class="sticky top-0 z-20 border-b border-border bg-card/95 backdrop-blur">
      <div class="mx-auto flex max-w-[1800px] items-center gap-3 px-4 py-2.5">
        <a routerLink="/" class="flex items-center gap-3" aria-label="DocDig">
          <app-logo />
          <span class="border-l border-border pl-3 text-lg font-semibold tracking-tight">DocDig</span>
        </a>
        <span class="flex-1"></span>

        <a routerLink="/review" routerLinkActive="bg-muted font-semibold"
           class="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm hover:bg-muted">
          {{ 'app.reviewQueue' | t }}
          @if (rt.pending().flags_left) {
            <span class="rounded-full bg-amber-600 px-1.5 text-xs font-medium text-white">{{ rt.pending().flags_left }}</span>
          }
        </a>

        <select class="input w-auto py-1" [attr.aria-label]="'app.language' | t"
                (change)="i18n.lang.set($any($event.target).value)">
          @for (l of langs; track l) { <option [value]="l" [selected]="l === i18n.lang()">{{ l.toUpperCase() }}</option> }
        </select>
        <select class="input w-auto py-1" [attr.aria-label]="'app.theme' | t"
                (change)="theme.mode.set($any($event.target).value)">
          @for (m of modes; track m) { <option [value]="m" [selected]="m === theme.mode()">{{ 'theme.' + m | t }}</option> }
        </select>
      </div>
    </header>
  `,
})
export class TopBar {
  protected i18n = inject(I18n);
  protected theme = inject(Theme);
  protected rt = inject(Realtime);
  protected langs: Lang[] = LANGS;
  protected modes: ThemeMode[] = THEME_MODES;
}
