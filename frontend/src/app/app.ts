import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { TopBar } from './layout/top-bar';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, TopBar],
  changeDetection: ChangeDetectionStrategy.OnPush,
  // Fixed-height shell: <main> is the scroll container, so pages can also fill exactly the viewport (review on xl).
  host: { class: 'flex h-dvh flex-col' },
  template: `
    <app-top-bar class="shrink-0" />
    <main class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto flex min-h-full max-w-[1800px] flex-col p-4 lg:p-5">
        <router-outlet />
      </div>
    </main>
  `,
})
export class App {}
