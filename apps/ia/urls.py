from django.urls import path
from apps.ia import views

app_name = 'ia'

urlpatterns = [
    path('asistente/', views.asistente_view, name='asistente'),
    path('api/chat/', views.chat_api, name='chat_api'),
]
