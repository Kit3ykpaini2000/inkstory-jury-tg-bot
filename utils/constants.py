"""
utils/constants.py — константы проекта
"""


class PostStatus:
    PENDING       = "pending"        # спарсен, ждёт жюри
    CHECKING      = "checking"       # жюри взял, работает прямо сейчас
    DONE          = "done"           # проверка завершена
    REJECTED      = "rejected"       # отклонён
    REVIEWER_POST = "reviewer_post"  # пост самого жюри, не проверяется

    ALL = (PENDING, CHECKING, DONE, REJECTED, REVIEWER_POST)
