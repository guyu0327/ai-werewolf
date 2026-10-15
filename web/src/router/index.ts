import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
    { path: '/game/:id', name: 'game', component: () => import('@/views/GameView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router
