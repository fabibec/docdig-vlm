import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { TopBar } from './layout/top-bar';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, TopBar],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-top-bar />
    <main class="mx-auto max-w-[1800px] p-4 lg:p-5">
      <router-outlet />
    </main>
  `,
})
export class App {}
