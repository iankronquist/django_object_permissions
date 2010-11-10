from warnings import warn

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django import db
from django.db import models

from models import ObjectPermission, ObjectPermissionType, UserGroup, \
    GroupObjectPermission
import object_permissions
from object_permissions.signals import granted, revoked


__all__ = ('register', 'grant', 'revoke', 'grant_group', 'revoke_group', \
               'get_user_perms', 'get_group_perms', 'get_model_perms', \
               'revoke_all', 'revoke_all_group', 'get_users', 'set_user_perms', \
               'set_group_perms', 'get_groups', 'filter_on_perms')

permission_map = {}
"""
A mapping of Models to Models. The key is a registered Model, and the value is
the Model that stores the permissions on that Model.
"""

permissions_for_model = {}
"""
A mapping of Models to lists of permissions defined for that model.
"""

_DELAYED = []
def register(perms, model):
    """
    Register permissions for a Model.

    The permissions should be a list of names of permissions, e.g. ["eat",
    "order", "pay"]. This function will insert a row into the permission table
    if one does not already exist.

    For backwards compatibility, this function can also take a single
    permission instead of a list. This feature should be considered
    deprecated; please fix your code if you depend on this.
    """

    if isinstance(perms, (str, unicode)):
        perms = [perms]

    try:
        _register(perms, model)
    except db.utils.DatabaseError:
        # there was an error, likely due to a missing table.  Delay this
        # registration.
        _DELAYED.append((perms, model))


def _register(perms, model):
    """
    Real method for registering permissions.

    This method is private; please don't call it from outside code.
    This inner function is required because its logic must also be available
    to call back from _register_delayed for delayed registrations.
    """

    if model in permission_map:
        warn("Tried to double-register %s for permissions!" % model)
        return

    name = "%s_Perms" % model.__name__
    fields = {
        "__module__": "",
        "user": models.ForeignKey(User),
        "group": models.ForeignKey(UserGroup, related_name="+"),
        "obj": models.ForeignKey(model),
    }
    for perm in perms:
        fields[perm] = models.BooleanField(default=False)

    class Meta:
        app_label = "object_permissions"

    fields["Meta"] = Meta

    perm_model = type(name, (models.Model,), fields)

    permission_map[model] = perm_model

def _register_delayed(**kwargs):
    """
    Register all permissions that were delayed waiting for database tables
    to be created
    """
    try:
        for args in _DELAYED:
            _register(*args)
        models.signals.post_syncdb.disconnect(_register_delayed)
    except db.utils.DatabaseError:
        # still waiting for models in other apps to be created
        pass

models.signals.post_syncdb.connect(_register_delayed)

def grant(user, perm, object):
    """
    Grants a permission to a User
    """

    model = object.__class__
    permissions = permission_map[model]
    properties = dict(user=user, object_id=object.id)

    user_perms = permissions.objects.filter(**properties)
    if not user_perms.exists():
        user_perms = permissions(**properties)

    # XXX could raise FieldDoesNotExist
    setattr(user_perms, perm, True)
    user_perms.save()

    granted.send(sender=user, perm=perm, object=object)


def grant_group(group, perm, object):
    """
    Grants a permission to a UserGroup
    """

    model = object.__class__
    permissions = permission_map[model]
    properties = dict(group=group, object_id=object.id)

    group_perms = permissions.objects.filter(**properties)
    if not group_perms.exists():
        group_perms = permissions(**properties)

    # XXX could raise FieldDoesNotExist
    setattr(group_perms, perm, True)
    group_perms.save()

    granted.send(sender=group, perm=perm, object=object)


def set_user_perms(user, perms, object):
    """
    Set perms to the list specified
    """

    model = object.__class__
    permissions = permission_map[model]
    all_perms = dict((p, False) for p in permissions_for_model[model])
    for perm in perms:
        all_perms[perm] = True

    user_perms = permissions.objects.filter(user=user, object_id=object.id)

    if user_perms.exists():
        for perm, enabled in all_perms.iteritems():
            setattr(user_perms, perm, enabled)
    else:
        user_perms = permissions(user=user, object_id=object.id,
                **dict(all_perms))
        user_perms.save()

    return perms


def set_group_perms(group, perms, object):
    """
    Set group's perms to the list specified
    """

    model = object.__class__
    permissions = permission_map[model]
    all_perms = dict((p, False) for p in permissions_for_model[model])
    for perm in perms:
        all_perms[perm] = True

    group_perms = permissions.objects.filter(group=group, object_id=object.id)

    if group_perms.exists():
        for perm, enabled in all_perms.iteritems():
            setattr(group_perms, perm, enabled)
    else:
        group_perms = permissions(group=group, object_id=object.id,
                **dict(all_perms))
        group_perms.save()

    return perms


def revoke(user, perm, object):
    """
    Revokes a permission from a User
    """

    ct = ContentType.objects.get_for_model(object)
    query = ObjectPermission.objects \
                .filter(user=user, object_id=object.id,  \
                    permission__content_type=ct, permission__name=perm)
    if query.exists():
        query.delete()
        revoked.send(sender=user, perm=perm, object=object)


def revoke_all(user, object):
    """
    Revokes all permissions from a User
    """
    ct = ContentType.objects.get_for_model(object)
    query = ObjectPermission.objects \
        .filter(user=user, object_id=object.id, permission__content_type=ct)
    if revoked.receivers:
        # only perform second query if there are receivers attached
        perms = list(query.values_list('permission__name', flat=True))
        query.delete()
        for perm in perms:
            revoked.send(sender=user, perm=perm, object=object)
    else:
        query.delete()


def revoke_all_group(group, object):
    """
    Revokes all permissions from a User
    """
    ct = ContentType.objects.get_for_model(object)
    query = GroupObjectPermission.objects \
        .filter(group=group, object_id=object.id, permission__content_type=ct)
    if revoked.receivers:
        # only perform second query if there are receivers attached
        perms = list(query.values_list('permission__name', flat=True))
        query.delete()
        for perm in perms:
            revoked.send(sender=group, perm=perm, object=object)
    else:
        query.delete()


def revoke_group(group, perm, object):
    """
    Revokes a permission from a UserGroup
    """
    ct = ContentType.objects.get_for_model(object)
    query = GroupObjectPermission.objects \
                .filter(group=group, object_id=object.id,  \
                    permission__content_type=ct, permission__name=perm) 
    if query.exists():
        query.delete()
        revoked.send(sender=group, perm=perm, object=object)


def get_user_perms(user, object):
    """
    Return a list of perms that a User has.
    """
    ct = ContentType.objects.get_for_model(object)
    query = ObjectPermission.objects \
        .filter(user=user, object_id=object.id, permission__content_type=ct) \
        .values_list('permission__name', flat=True)
    return list(query)


def get_group_perms(group, object):
    """
    Return a list of perms that a UserGroup has.
    """
    ct = ContentType.objects.get_for_model(object)
    query = GroupObjectPermission.objects \
        .filter(group=group, object_id=object.id, permission__content_type=ct) \
        .values_list('permission__name', flat=True)
    return list(query)


def get_model_perms(model):
    """
    Return a list of perms that a model has registered
    """

    return permissions_for_model[model]


def group_has_perm(group, perm, object):
    """
    check if a UserGroup has a permission on an object
    """
    if object is None:
        return False
    
    ct = ContentType.objects.get_for_model(object)
    return GroupObjectPermission.objects \
        .filter(group=group, object_id=object.id, \
                permission__name=perm, permission__content_type=ct) \
        .exists()


def get_users(object):
    """
    Return a list of Users with permissions directly on a given object.  This
    will not include users that have permissions via a UserGroup
    """
    ct = ContentType.objects.get_for_model(object)
    return User.objects.filter(
            object_permissions__permission__content_type=ct, \
            object_permissions__object_id=object.id).distinct()


def get_groups(object):
    """
    Return a list of UserGroups with permissions on a given object
    """
    ct = ContentType.objects.get_for_model(object)
    return UserGroup.objects.filter(
            object_permissions__permission__content_type=ct, \
            object_permissions__object_id=object.id).distinct()


def perms_on_any(user, model, perms, groups=True):
    """
    Determines whether the user has any of the listed perms on any instances of
    the Model.  This checks both user permissions and group permissions.
    
    @param user: user who must have permissions
    @param model: model on which to filter
    @param perms: list of perms to match
    @return true if has perms on any instance of model
    """
    ct = ContentType.objects.get_for_model(model)
    
    # permissions user has
    if ObjectPermission.objects.filter(
            user = user,
            permission__content_type=ct,
            permission__name__in=perms
        ).exists():
            return True
    
    # permissions user's groups have
    if groups:
        if GroupObjectPermission.objects.filter(
                group__users = user,
                permission__content_type=ct,
                permission__name__in=perms
            ).exists():
                return True
    
    return False


def filter_on_perms(user, model, perms, groups=True, **clauses):
    """
    Filters objects that the User has permissions on.  This includes any objects
    the user has permissions based on belonging to a UserGroup.
    
    @param user: user who must have permissions
    @param model: model on which to filter
    @param perms: list of perms to match
    @param groups: include perms the user has from membership in UserGroups
    @param clauses: additional clauses to be added to the queryset
    @return a queryset of matching objects
    """
    ct = ContentType.objects.get_for_model(model)
    
    # permissions user has
    ids = list(ObjectPermission.objects.filter(
            user=user,
            permission__content_type=ct,
            permission__name__in=perms
        ).values_list('object_id', flat=True))
    
    # permissions user's groups have
    if groups:
        ids += list(GroupObjectPermission.objects.filter(
                group__users=user,
                permission__content_type=ct,
                permission__name__in=perms
            ).values_list('object_id', flat=True))
    
    return model.objects.filter(id__in=ids, **clauses)


def filter_on_group_perms(usergroup, model, perms, **clauses):
    """
    Filters objects that the UserGroup has permissions on.
    
    @param usergroup: UserGroup who must have permissions
    @param model: model on which to filter
    @param perms: list of perms to match
    @param clauses: additional clauses to be added to the queryset
    @return a queryset of matching objects
    """
    ct = ContentType.objects.get_for_model(model)
    
    # permissions user's groups have
    ids = list(GroupObjectPermission.objects.filter(
            group=usergroup,
            permission__content_type=ct,
            permission__name__in=perms
        ).values_list('object_id', flat=True))
    
    return model.objects.filter(id__in=ids, **clauses)


# register internal perms
register(['admin'], UserGroup)


# make some methods available as bound methods
setattr(User, 'grant', grant)
setattr(User, 'revoke', revoke)
setattr(User, 'revoke_all', revoke_all)
setattr(User, 'get_perms', get_user_perms)
setattr(User, 'set_perms', set_user_perms)
setattr(User, 'filter_on_perms', filter_on_perms)
setattr(User, 'perms_on_any', perms_on_any)

setattr(UserGroup, 'grant', grant_group)
setattr(UserGroup, 'revoke', revoke_group)
setattr(UserGroup, 'revoke_all', revoke_all_group)
setattr(UserGroup, 'has_perm', group_has_perm)
setattr(UserGroup, 'get_perms', get_group_perms)
setattr(UserGroup, 'set_perms', set_group_perms)
setattr(UserGroup, 'filter_on_perms', filter_on_group_perms)
