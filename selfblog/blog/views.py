# coding:utf-8
import logging

from django.db.models import Q
from django.db.models import F
from django.core.paginator import Paginator
from django.views.generic import ListView, DetailView
from django.shortcuts import render
from ipware.ip import get_real_ip

from django.conf import settings
from .models import Post, Category, Page, Widget
from utils.cache import LRUCacheDict, cache

logger = logging.getLogger(__name__)


class BaseMixin(object):

    def get_context_data(self, *args, **kwargs):
        if 'object' in kwargs or 'query' in kwargs:
            context = super(BaseMixin, self).get_context_data(**kwargs)
        else:
            context = {}

        try:
            context['categories'] = Category.available_list()
            context['widgets'] = Widget.available_list()
            context['recently_posts'] = Post.get_recently_posts(settings.RECENTLY_NUM)
            context['hot_posts'] = Post.get_hots_posts(settings.HOT_NUM)
            context['pages'] = Page.objects.filter(status=0)
            context['online_num'] = len(cache.get('online_ips', []))
        except Exception as e:
            logger.exception(u'加载基本信息出错[%s]！', e)

        return context


class IndexView(BaseMixin, ListView):
    query = None
    template_name = 'index.html'

    def get(self, request, *args, **kwargs):
        try:
            self.cur_page = int(request.GET.get('page', 1))
        except TypeError:
            self.cur_page = 1

        if self.cur_page < 1:
            self.cur_page = 1

        return super(IndexView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        paginator = Paginator(self.object_list, settings.PAGE_NUM)
        kwargs['posts'] = paginator.page(self.cur_page)
        kwargs['query'] = self.query
        return super(IndexView, self).get_context_data(**kwargs)

    def get_queryset(self):
        self.query = self.request.GET.get('s')
        if self.query:
            qset = (
                Q(title__icontains=self.query) |
                Q(content__icontains=self.query)
            )
            posts = Post.objects.defer('content', 'content_html')\
                .filter(qset, status=0)
            for post in posts:
                post.title = post.title.replace(self.query, '<span class="hightline">%s</span>' % self.query)
                post.summary = post.summary.replace(self.query, '<span class="hightline">%s</span>' % self.query)
        else:
            posts = Post.objects.defer('content', 'content_html')\
                .filter(status=0)

        return posts


class CategoryListView(IndexView):
    def get_queryset(self):
        alias = self.kwargs.get('alias')

        try:
            self.category = Category.objects.get(alias=alias)
        except Category.DoesNotExist:
            return []

        posts = self.category.post_set.defer('content', 'content_html').filter(status=0)
        return posts

    def get_context_data(self, **kwargs):
        if hasattr(self, 'category'):
            kwargs['title'] = self.category.name + ' | '

        return super(CategoryListView, self).get_context_data(**kwargs)


class TagsListView(IndexView):
    def get_queryset(self):
        self.tag = self.kwargs.get('tag')
        posts = Post.objects.defer('content', 'content_html')\
            .filter(tags__icontains=self.tag, status=0)
        return posts

    def get_context_data(self, **kwargs):
        kwargs['title'] = self.tag + ' | '
        return super(TagsListView, self).get_context_data(**kwargs)


class PostDetailView(BaseMixin, DetailView):
    object = None
    template_name = 'detail.html'
    queryset = Post.objects.filter(status=0)
    slug_field = 'alias'

    def get(self, request, *args, **kwargs):
        ip = get_real_ip
        self.cur_user_ip = ip

        alias = self.kwargs.get('slug')
        alias = alias.replace(' ', '')
        try:
            self.object = self.queryset.get(alias=alias)
        except Post.DoesNotExist:
            referer = request.META.get('HTTP_REFERER')
            logger.error(u'ref[%s] [%s]访问不存在的文章：[%s]', referer, ip, alias)
            context = super(PostDetailView, self).get_context_data(**kwargs)
            return render(request, '404.html', context)

        visited_ips = cache.get(self.object.id, [])

        if ip not in visited_ips:
            Post.objects.filter(id=self.object.id).update(view_times=F('view_times')+1)

            visited_ips.append(ip)

            self.set_lru_read(ip, self.object)

            DAY = 24 * 60  # 一天
            cache.set(self.object.id, visited_ips, DAY)

        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def set_lru_read(self, ip, post):
        # 保存别人正在读
        lru_views = cache.get('lru_views')
        if not lru_views:
            lru_views = LRUCacheDict(max_size=10, expiration=settings.FIF_MIN)

        if post not in lru_views.values():
            lru_views[ip] = post

        cache.set('lru_views', lru_views, settings.FIF_MIN)

    def get_context_data(self, **kwargs):
        context = super(PostDetailView, self).get_context_data(**kwargs)

        context['lru_views'] = cache.get('lru_views', {}).items()
        context['cur_user_ip'] = self.cur_user_ip

        context['related_posts'] = self.object.related_posts

        return context


class PageDetailView(BaseMixin, DetailView):
    template_name = "page.html"
    queryset = Page.objects.filter(status=0)
    slug_field = 'alias'

    def get(self, request, *args, **kwargs):
        alias = self.kwargs.get('slug')
        try:
            self.object = self.queryset.get(alias=alias)
            context = self.get_context_data(object=self.object)
        except Page.DoesNotExist:
            logger.error(u'访问不存在的页面：[%s]' % alias)
            context = self.get_context_data(**kwargs)
            return render(request, '404.html', context)

        return self.render_to_response(context)
